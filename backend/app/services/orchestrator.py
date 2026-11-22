from __future__ import annotations

import json
import hashlib
import math
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..models import AssetRecord, Shot, Task, TaskInput, TraceRecord, now_iso, new_id
from ..providers.audio_runtime import AudioRuntimeClient
from ..providers.media_ffmpeg import validate_audio_file
from ..providers.music import select_music
from ..providers.registry import ProviderRegistry, build_provider_registry, invoke_create_or_find
from ..storage import Storage, TaskBusyError, library_provenance
from .subject_review import reference_path, review_group
from .visual_review import build_review, write_json
from .cost import billing_scope
from .subtitle_alignment import align_subtitles
from . import consistency
from . import knowledge as zhejiang_kb
from . import reference_images
from . import visual_qa
from .audio_sync import PerShotAudioBuilder
from .library import library_asset_record
from .voice_studio import validate_clip, plan_track
from ..providers.contract import SHOT_TAGS, TRANSITIONS


NO_VOICE = "\u65e0\u914d\u97f3"
NO_MUSIC = "\u65e0\u97f3\u4e50"

# \u9700\u8981\u9996\u5e27\u56fe\u7684\u7d20\u6750 provider\u3002\u56fe\u751f\u89c6\u9891\u5fc5\u987b\u6709\u9996\u5e27\uff1b\u6587\u751f\u89c6\u9891\u81ea\u5df1\u7ec4\u7ec7\u753b\u9762\uff0c\u4e0d\u9700\u8981\uff0c
# \u7ed9\u5b83\u5148\u505a\u4e00\u6b21\u6587\u751f\u56fe\u7eaf\u5c5e\u591a\u4ed8\u4e00\u6b21\u94b1\u3002
FRAME_REQUIRED_PROVIDERS = frozenset({"ai-video", "cloud-i2v", "cloud"})
# \u8fd9\u51e0\u6761\u6765\u6e90\u53ea\u8d70\u964d\u7ea7\u94fe\u7b2c\u4e00\u6863\uff1a\u751f\u6210\u5931\u8d25\u5c31\u62a5\u9519\uff0c\u4e0d\u9000\u56de\u672c\u5730\u5360\u4f4d\u56fe\u3002
STRICT_ASSET_SOURCES = frozenset(
    {"ai", "cloud", "ai-video", "cloud-i2v", "ai-t2v", "cloud-t2v"}
)

@dataclass(frozen=True)
class StageSpec:
    name: str
    label: str
    started_progress: int
    completed_progress: int


PIPELINE: tuple[StageSpec, ...] = (
    StageSpec("scripting", "脚本", 12, 27),
    StageSpec("storyboard", "分镜", 32, 36),
    StageSpec("audio", "配音", 48, 58),
    StageSpec("assets", "画面", 67, 76),
    StageSpec("rendering", "渲染", 84, 96),
)


class Orchestrator:
    def __init__(self, settings: Settings, storage: Storage, registry: ProviderRegistry | None = None) -> None:
        self.settings = settings
        self.storage = storage
        self.registry = registry or build_provider_registry(settings, storage)
        # P3 逐镜配音：合成 → ffprobe 实测 cues → 镜间 150ms 静音。runtime 可选，
        # 缺失时按 TTS_PROVIDER 走系统语音或云 TTS。
        self.audio_runtime = AudioRuntimeClient.from_settings(settings)
        self.audio_builder = PerShotAudioBuilder(settings, self.audio_runtime)
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.RLock()
        # 逐镜素材并发生成时，task.assets / trace.events / 进度都会被多个线程碰。
        # 用同一把锁保护，避免交错写坏任务快照。
        self._state_lock = threading.RLock()

    def system_config(self) -> dict[str, Any]:
        return {
            "providers": self.registry.describe(),
            "selected": {
                "llm": self.registry.resolve_name("llm", self.settings.default_provider),
                "asset": self.registry.resolve_name("asset", self.settings.asset_provider),
                "tts": self.settings.tts_provider,
                "music": self.settings.music_provider,
                "render": self.registry.resolve_name("render", self.settings.render_provider),
            },
            "fallbacks": {
                "llm": list(self.settings.llm_fallback_providers) if self.settings.allow_provider_fallback else [],
                "tts": list(self.settings.tts_fallback_providers),
                **self.registry.describe_chains(),
            },
            "pipeline": [
                {"name": item.name, "label": item.label, "order": index + 1}
                for index, item in enumerate(PIPELINE)
            ] + [{"name": "review", "label": "审核", "order": len(PIPELINE) + 1}],
        }

    def has_running_tasks(self) -> bool:
        with self._threads_lock:
            return any(worker.is_alive() for worker in self._threads.values())

    def create_task(self, task_input: TaskInput) -> Task:
        task = Task(id=new_id("task"), input=task_input, initial_input=vars(task_input).copy())
        task.trace = TraceRecord(
            provider="pending",
            model="pending",
            prompt_version=consistency.PROMPT_VERSION,
            fallback_used=False,
        )
        self.storage.save_task(task)
        self.start(task.id)
        return task

    def create_preview(self, task_input: TaskInput) -> Task:
        task = Task(
            id=new_id("draft"), input=task_input, initial_input=vars(task_input).copy(),
            status="draft", stage="preview", progress=5,
        )
        task.trace = TraceRecord(
            provider="pending",
            model="pending",
            prompt_version=consistency.PROMPT_VERSION,
            fallback_used=False,
        )
        self.storage.save_task(task)
        try:
            self._scripting(task)
            self.update(
                task,
                status="draft",
                stage="preview",
                progress=35,
                event={
                    "type": "preview",
                    "status": "ready",
                    "message": "Script and storyboard are ready for review",
                },
            )
        except Exception as exc:
            task.status = "failed"
            task.stage = "preview"
            task.error = str(exc)
            self.update(task, event={"type": "error", "message": str(exc)})
        return task

    def generate(self, task_id: str) -> dict[str, Any]:
        task = self._load(task_id)
        if task.status == "running":
            raise ValueError("Task is already running")
        if task.status not in ("draft", "edited", "failed"):
            raise ValueError("Only a draft task can be generated")
        if not task.shots:
            raise ValueError("The draft has no storyboard")
        if task.input.voice_engine == "reselect":
            raise ValueError("原声库已停用，请重新选择音色后合成")
        self.start(task_id, "storyboard")
        return {"task_id": task_id, "accepted": True, "from_stage": "storyboard"}

    def voice_preview(self, task_id: str, payload: dict[str, Any], *, synthesize: bool) -> dict[str, Any]:
        """Preview unsaved narration in an isolated directory, without editing the task."""
        task = self._load(task_id)
        if task.status == "running":
            raise ValueError("视频生成中，请完成后再试听")
        if not task.shots:
            raise ValueError("请先生成分镜脚本")
        if not isinstance(payload.get("input", {}), dict) or not isinstance(payload.get("shots", []), list):
            raise ValueError("input 必须是对象，shots 必须是列表")
        task.input = TaskInput.from_dict({**vars(task.input), **payload.get("input", {})})
        patches = payload.get("shots", [])
        if len(patches) > len(task.shots):
            raise ValueError("片段数量超出分镜数量")
        by_id = {shot.id: shot for shot in task.shots}
        seen = set()
        for patch in patches:
            if not isinstance(patch, dict) or patch.get("id") not in by_id or patch["id"] in seen:
                raise ValueError("片段 ID 无效或重复")
            seen.add(patch["id"])
            shot = by_id[patch["id"]]
            for key in ("narration", "subtitle"):
                if key in patch:
                    value = str(patch[key]).strip()
                    if not value or len(value) > 1000:
                        raise ValueError(f"{key} 不能为空或超过 1000 字")
                    setattr(shot, key, value)
            for key, value in validate_clip({**vars(shot), **patch}).items():
                setattr(shot, key, value)
        selected_id = payload.get("shot_id")
        if selected_id:
            if selected_id not in by_id:
                raise ValueError("试听片段不存在")
            task.shots = [by_id[selected_id]]
        track = plan_track(task.shots, task.input)
        if not synthesize:
            return track
        if task.input.voice_engine == "reselect":
            raise ValueError("原声库已停用，请重新选择音色后合成")
        if task.input.voice == NO_VOICE:
            raise ValueError("当前选择无配音，请先选择音色")
        directory = self.settings.media_dir / task.id / "voice-previews" / new_id("preview")
        billing_events = []

        def record_preview(event):
            billing_events.append({**event, "purpose": "voice_preview",
                                   **({"shot_id": selected_id} if selected_id else {})})
            directory.mkdir(parents=True, exist_ok=True)
            write_json(directory / "billing.json", {"events": billing_events})

        with billing_scope(record_preview):
            result = self.audio_builder.build(
                task.id, task.shots, voice=task.input.voice, rate=task.input.voice_rate,
                pitch=task.input.voice_pitch, style=task.input.voice_style,
                expressiveness=task.input.voice_expressiveness,
                force_provider="" if task.input.voice_engine == "configured" else task.input.voice_engine,
                target_duration=0 if selected_id else task.input.duration, output_dir=directory,
            )
        track = plan_track(task.shots, task.input)
        track["timing"] = "measured"
        for clip in track["clips"]:
            measured = [trace for trace in result.provider_trace if trace.get("shot_id") == clip["shot_id"]]
            for phrase, trace in zip(clip["phrases"], measured):
                if trace.get("provider") != "local-upload":
                    phrase.update(trace.get("prosody", {}))
        warnings = []
        for trace in result.provider_trace:
            if trace.get("fallback_errors"):
                warnings.append("联网语音失败，本次已使用系统语音，音色与自然度可能不同。")
            if trace.get("provider") in {"windows-system-speech", "openai-compatible-tts"}:
                warnings.append("当前引擎未应用音高参数；下方显示的是规划值。")
        if result.total_duration > task.input.duration + 0.05 and not selected_id:
            warnings.append("旁白超过目标时长，请缩短文本或调整语速后再生成视频。")
        return {**track, "audio_url": self._media_url(task.id, Path(result.voice_path)),
                "duration": result.total_duration, "traces": result.provider_trace,
                "provider": result.event.get("voice_provider"), "model": result.event.get("voice_model"),
                "rate_scale": result.event.get("rate_scale", 1), "warnings": list(dict.fromkeys(warnings))}

    def update_draft(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        task = self._load(task_id)
        if task.status not in ("draft", "edited", "failed"):
            raise ValueError("Only a draft task can be edited")
        original_duration = task.input.duration
        original_durations = {shot.id: shot.duration_sec for shot in task.shots}
        if isinstance(patch.get("input"), dict):
            merged_input = task.input.__dict__.copy()
            merged_input.update(patch["input"])
            task.input = TaskInput.from_dict(merged_input)
        for key in ("title", "summary", "cta", "narration"):
            if key in patch:
                value = str(patch[key]).strip()
                if not value or len(value) > 5000:
                    raise ValueError(f"{key} cannot be empty or longer than 5000 characters")
                setattr(task, key, value)
        if "tags" in patch:
            if not isinstance(patch["tags"], list):
                raise ValueError("tags must be a list")
            task.tags = [str(item).strip() for item in patch["tags"] if str(item).strip()][:20]
        if "shots" in patch:
            if not isinstance(patch["shots"], list):
                raise ValueError("shots must be a list")
            seen = set()
            for shot_patch in patch["shots"]:
                if not isinstance(shot_patch, dict) or not isinstance(shot_patch.get("id"), str) or shot_patch["id"] in seen:
                    raise ValueError("shots must contain unique shot ids")
                seen.add(shot_patch["id"])
                self._patch_shot(task, shot_patch["id"], shot_patch)
        changed_duration = any(
            abs(float(shot.duration_sec) - float(original_durations[shot.id])) > 0.000001
            for shot in task.shots
        )
        if (changed_duration or task.input.duration != original_duration) and abs(
            sum(shot.duration_sec for shot in task.shots) - task.input.duration
        ) > 0.01:
            raise ValueError(f"Shot durations must add up to the task duration ({task.input.duration}s)")
        task.status = "draft"
        task.stage = "preview"
        task.progress = 35
        task.error = ""
        self.update(task, event={"type": "preview", "status": "edited"})
        return task.to_dict()

    def trash_task(self, task_id: str) -> str:
        with self._threads_lock:
            current = self._threads.get(task_id)
            if current and current.is_alive():
                raise TaskBusyError("任务正在生成，请完成后再删除。")
            return self.storage.trash_task(task_id)

    def start(self, task_id: str, from_stage: str | None = None) -> None:
        with self._threads_lock:
            if not self.storage.get_task(task_id):
                raise KeyError("任务不存在")
            current = self._threads.get(task_id)
            if current and current.is_alive():
                return
            thread = threading.Thread(target=self._run, args=(task_id, from_stage), daemon=True)
            self._threads[task_id] = thread
            thread.start()

    def retry(self, task_id: str, from_stage: str = "scripting") -> dict[str, Any]:
        task = self.storage.get_task(task_id)
        if not task:
            raise KeyError("Task does not exist")
        if task.get("status") == "running":
            raise ValueError("Task is already running")
        if from_stage not in {stage.name for stage in PIPELINE}:
            raise ValueError("Unsupported retry stage")
        self.start(task_id, from_stage)
        return {"task_id": task_id, "from_stage": from_stage, "accepted": True}

    def _load(self, task_id: str) -> Task:
        data = self.storage.get_task(task_id)
        if not data:
            raise KeyError("Task does not exist")
        return self._task_from_dict(data)

    @staticmethod
    def _task_from_dict(data: dict[str, Any]) -> Task:
        # Preserve the old defaults for records saved before these fields were present.
        saved_input = {"output_resolution": "720x1280", "fps": 25, "music_ducking": "不压低", **data["input"]}
        task = Task(
            id=data["id"],
            input=TaskInput.from_dict(saved_input),
            initial_input=data.get("initial_input"),
            status=data.get("status", "queued"),
            stage=data.get("stage", "queued"),
            progress=int(data.get("progress", 0)),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            cta=data.get("cta", ""),
            tags=list(data.get("tags", [])),
            narration=data.get("narration", ""),
            audio=dict(data.get("audio", {})),
            render=dict(data.get("render", {})),
            visual_qa=dict(data.get("visual_qa", {})),
            error=data.get("error", ""),
            created_at=data.get("created_at", now_iso()),
            updated_at=data.get("updated_at", now_iso()),
            billing_records=list(data.get("billing_records", [])),
            publication_observations=list(data.get("publication_observations", [])),
            execution_runs=list(data.get("execution_runs", [])),
        )
        task.shots = [Shot(**shot) for shot in data.get("shots", [])]
        task.assets = [AssetRecord(**asset) for asset in data.get("assets", [])]
        trace = data.get("trace")
        task.trace = TraceRecord(**trace) if trace else None
        return task

    def update(
        self,
        task: Task,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        event: dict[str, Any] | None = None,
    ) -> None:
        with self._state_lock:
            if status:
                task.status = status
            if stage:
                task.stage = stage
            if progress is not None:
                task.progress = max(0, min(100, progress))
            task.updated_at = now_iso()
            if event and task.trace:
                task.trace.events.append(event)
            self.storage.save_task(task)

    def _run(self, task_id: str, from_stage: str | None) -> None:
        task = self._load(task_id)
        run_id = new_id("run")
        started_at = datetime.now(timezone.utc)
        run_entry: dict[str, Any] = {
            "run_id": run_id,
            "started_at": started_at.isoformat(),
            "status": "running",
            "stage": from_stage or PIPELINE[0].name,
        }
        task.execution_runs.append(run_entry)
        self.update(task)
        try:
            selected_stage = from_stage or PIPELINE[0].name
            stage_names = [stage.name for stage in PIPELINE]
            if selected_stage not in stage_names:
                raise ValueError(f"Unsupported pipeline stage: {selected_stage}")
            # 重跑时必须清掉上一次的错误和降级标记。前端只按 task.error 是否有值
            # 决定要不要显示失败框，不清的话任务明明在跑、界面上还挂着旧报错；
            # fallback_used 描述的是"本次运行有没有降级"，留着上次的值会让一次
            # 完全走主链路的任务被误判成降级产物。历史事件保留，不动 trace.events。
            task.error = ""
            if task.trace:
                task.trace.fallback_used = False
            self.update(task, status="running", stage=selected_stage, progress=5)
            handlers: dict[str, Callable[[Task], None]] = {
                "scripting": self._scripting,
                "storyboard": self._storyboard,
                "audio": self._audio,
                "assets": self._assets,
                "rendering": self._render,
            }
            for stage in PIPELINE[stage_names.index(selected_stage):]:
                handlers[stage.name](task)
            self.update(task, status="completed", stage="review", progress=100)
        except Exception as exc:
            task.error = str(exc)
            self.update(
                task,
                status="failed",
                stage=task.stage,
                progress=max(task.progress, 1),
                event={
                    "type": "error",
                    "message": str(exc),
                },
            )
        finally:
            elapsed = max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
            run_entry.update(
                finished_at=datetime.now(timezone.utc).isoformat(),
                duration_seconds=round(elapsed, 3),
                status="failed" if task.status == "failed" else "completed",
                stage=task.stage,
            )
            self.update(task)

    def _scripting(self, task: Task) -> None:
        self.update(
            task,
            stage="scripting",
            progress=12,
            event={"type": "stage", "stage": "scripting", "status": "started"},
        )
        prompt = json.dumps(task.input.__dict__, ensure_ascii=False, sort_keys=True)
        selected = self.settings.default_provider.lower()
        candidates = [selected]
        if self.settings.allow_provider_fallback:
            candidates.extend(self.settings.llm_fallback_providers)
        errors: list[str] = []
        result = None
        for candidate in dict.fromkeys(candidates):
            try:
                provider = self.registry.create("llm", candidate)
                with billing_scope(lambda event: self.update(task, event=event)):
                    result = provider.generate(prompt, task.input.__dict__)
                if errors:
                    result.fallback_used = True
                    result.event["fallback_reason"] = "; ".join(errors)
                break
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
        if result is None:
            raise RuntimeError(f"No LLM provider succeeded: {'; '.join(errors)}")
        normalized_shots = self._normalize_shots(
            list(result.data.get("shots") or []),
            task.input.duration,
        )
        task.title = str(result.data["title"])
        task.summary = str(result.data["summary"])
        task.cta = str(result.data["cta"])
        task.tags = list(result.data["tags"])
        task.narration = str(result.data["narration"])
        task.shots = [Shot(**item) for item in normalized_shots]
        trace_event = dict(result.event)
        trace_event.setdefault("type", "llm")
        trace_event.setdefault("cost_reported", bool(trace_event.get("cost_reported", False)))
        task.trace = TraceRecord(
            provider=result.provider,
            model=result.model,
            prompt_version=result.event.get("prompt_version", consistency.PROMPT_VERSION),
            fallback_used=result.fallback_used,
            events=[*(task.trace.events if task.trace else []), trace_event],
        )
        task.audio["knowledge"] = result.data.get("knowledge", {})
        task.audio["knowledge_references"] = [zhejiang_kb.source_record(entity) for entity in
            zhejiang_kb.topic_entities(task.input.city, task.input.landmark, task.input.theme, task.input.culture, task.input.festival)]
        self.update(
            task,
            stage="scripting",
            progress=27,
            event={"type": "stage", "stage": "scripting", "status": "completed"},
        )

    @staticmethod
    def _normalize_shots(shots: list[dict[str, Any]], duration: int) -> list[dict[str, Any]]:
        if not shots:
            raise ValueError("Provider returned no shots")
        total = sum(max(0.1, float(item.get("duration_sec", 0))) for item in shots)
        elapsed = 0.0
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(shots, start=1):
            copy = dict(item)
            copy["id"] = str(copy.get("id") or f"shot_{index}")
            copy["order"] = index
            if index == len(shots):
                value = round(duration - elapsed, 2)
            else:
                value = round(duration * max(0.1, float(copy.get("duration_sec", 0))) / total, 2)
            if value < 1:
                value = 1.0
            copy["duration_sec"] = value
            elapsed += value
            normalized.append(copy)
        normalized[-1]["duration_sec"] = round(
            normalized[-1]["duration_sec"] + duration - elapsed, 2
        )
        if normalized[-1]["duration_sec"] < 1:
            raise ValueError("Provider shot durations cannot fit the requested duration")
        # A model often repeats "fade" for every shot. Set a varied default
        # during initial drafting; subsequent user edits remain untouched.
        if len({str(item.get("transition") or "fade") for item in normalized[1:]}) <= 1:
            styles = ("smoothleft", "fade", "dissolve", "circleopen", "wipeleft")
            for index, item in enumerate(normalized[1:]):
                item["transition"] = styles[index % len(styles)]
        return normalized

    def _storyboard(self, task: Task) -> None:
        self.update(
            task,
            stage="storyboard",
            progress=36,
            event={
                "type": "stage",
                "stage": "storyboard",
                "status": "completed",
                "shot_count": len(task.shots),
            },
        )

    def _assets(self, task: Task) -> None:
        self.update(
            task,
            stage="assets",
            progress=67,
            event={"type": "stage", "stage": "assets", "status": "started"},
        )
        directory = self.settings.media_dir / task.id / "assets"
        knowledge = task.audio.get("knowledge", {})
        asset_source = task.input.asset_source
        # 素材来源 → 降级链（P2 的 cloud-i2v → cloud-t2i → local-library → offline）
        candidates = tuple(self.registry.asset_chain_for(asset_source))
        # P2 一致性策略：统一调色参数（进 RenderRequest.grade），画面 provider 与渲染线共用
        style_ctx = {
            "task_id": task.id,
            "city": task.input.city,
            "landmark": task.input.landmark,
            "theme": task.input.theme,
            "culture": task.input.culture,
            "style": task.input.style,
            "color": knowledge.get("color", "#2d6c63"),
            "accent": knowledge.get("accent", "#d6ad60"),
            "subject_lock": task.input.subject_lock,
            "subject_reference_path": task.input.subject_reference_path,
            "subject_lock_required": bool(task.input.subject_lock),
            "image_reference_field": getattr(self.settings, "image_reference_field", "image"),
            "pexels_media_mode": getattr(self.settings, "pexels_media_mode", "video-first"),
            # 成片规格单独放一份；resolution / ratio / size 是 provider 认的口径，
            # 直接塞 "720x1280" 会被 video.normalize_resolution 拒掉。
            "output_resolution": task.input.output_resolution,
            "visual_usage": task.input.visual_usage,
            "city": task.input.city,
        }
        style_ctx.update(
            consistency.output_spec(
                task.input.output_resolution, fallback_size=self.settings.image_size
            )
        )
        style_ctx["grade"] = consistency.grade(style_ctx)
        task.audio["grade"] = style_ctx["grade"]
        existing_assets = {asset.shot_id: asset for asset in task.assets}
        groups = {}
        references = {}
        for shot in task.shots:
            if shot.subject_group:
                groups.setdefault(shot.subject_group, []).append(shot)
                if shot.subject_reference:
                    path = reference_path(directory.parent/"references", shot.subject_reference)
                    if references.setdefault(shot.subject_group,path) != path:
                        raise ValueError(f"主体组 {shot.subject_group} 绑定了多张参考图，请统一")
        if groups and candidates[0] in FRAME_REQUIRED_PROVIDERS and groups.keys()-references.keys():
            raise ValueError("请给每个主体组上传一张参考图")
        task.assets = []
        used_sources: set[str] = set()
        strict_dynamic = asset_source in STRICT_ASSET_SOURCES

        # 三个阶段：先规划（哪些镜复用、哪些要生成），再并发生成，最后按镜头顺序提交。
        # 提交必须串行且有序——task.assets 的顺序就是成片里镜头的顺序。
        # 单镜实测约 110 秒（文生图 37~70s + 图生视频 44s），串行四镜要八分钟。
        reused: dict[str, dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        generated: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        stock_source = candidates[0] in {"stock", "pixabay", "pexels"}
        for shot in task.shots:
            # 正式发布模式与显式绑定库素材的镜头：直接从本地素材库取已审核条目（PR #3）。
            if task.input.visual_usage == "publication" or shot.library_asset_id:
                item = self._plan_shot(task, shot)
                try:
                    asset = self.registry.generate_asset(
                        "local-library", task.id, item["payload"], directory, style_ctx
                    )
                    generated[shot.id] = {
                        "shot_id": shot.id,
                        "order": shot.order,
                        "asset": asset,
                        "used_sources": ["local-library"],
                        "events": [],
                        "error": "",
                    }
                except Exception as exc:
                    errors.append(f"Shot {shot.order} ({shot.id}): {exc}")
                continue
            item = self._plan_shot(task, shot)
            reference = references.get(shot.subject_group, "") if candidates[0] in FRAME_REQUIRED_PROVIDERS else ""
            selection_key = ""
            if reference or stock_source:
                selection = [candidates[0], task.input.city, task.input.landmark, task.input.style,
                             task.input.output_resolution, shot.title, shot.visual_prompt, item["payload"].get("search_query"),
                             shot.duration_sec, reference]
                selection_key = hashlib.sha256(json.dumps(selection, ensure_ascii=False).encode()).hexdigest()
                item["payload"]["selection_key"] = selection_key
            existing = existing_assets.get(shot.id)
            if (existing and (not stock_source or existing.page_url) and existing.selection_key == selection_key
                    and self._is_reusable(existing, strict_dynamic)):
                reused[shot.id] = self._reused_asset(existing)
            else:
                if reference:
                    item["payload"]["first_frame_path"] = reference
                pending.append(item)

        if stock_source:
            style_ctx.update(stock_ids={a.get("external_id") or a["id"] for a in reused.values()}, stock_lock=threading.Lock())

        if pending:
            workers = max(1, min(self.settings.asset_concurrency, len(pending)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(
                        self._generate_shot,
                        task,
                        item,
                        directory,
                        knowledge,
                        style_ctx,
                        candidates,
                        strict_dynamic,
                    )
                    for item in pending
                ]
                for future in as_completed(futures):
                    result = future.result()
                    generated[result["shot_id"]] = result
                    self.update(
                        task,
                        stage="assets",
                        progress=min(
                            75,
                            67 + int(8 * len(generated) / max(len(task.shots), 1)),
                        ),
                        event={
                            "type": "shot",
                            "stage": "assets",
                            "shot_id": result["shot_id"],
                            "status": "failed" if result["error"] else "completed",
                            "order": result["order"],
                        },
                    )

        for shot in task.shots:
            result = generated.get(shot.id)
            if result is not None:
                if result["error"]:
                    errors.append(result["error"])
                    continue
                used_sources.update(result["used_sources"])
                if result["events"]:
                    with self._state_lock:
                        if task.trace:
                            task.trace.fallback_used = True
                            task.trace.events.extend(result["events"])
                asset = result["asset"]
            elif shot.id in reused:
                asset = reused[shot.id]
            else:
                continue
            self._commit_shot(task, shot, asset)
        # 有镜头失败也先把成功的提交完：素材已经落盘，重试时能直接复用，
        # 不用把已经付过费的那几镜重跑一遍。
        if errors:
            raise RuntimeError("; ".join(errors))
        reviews = []
        for group, members in groups.items():
            try:
                sheet = review_group(group, members, directory.parent)
                sheet["url"] = f"/media/{task.id}/{sheet['file']}"
                reviews.append(sheet)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                reviews.append({"group":group,"error":str(exc)})
        task.audio["subject_reviews"] = reviews
        primary_source = candidates[0] if candidates else asset_source
        requires_video = primary_source in {"pexels", "ai-video", "ai-t2v"}
        min_video_shots = (
            int(getattr(self.settings, "visual_qa_min_video_shots", 4))
            if requires_video else 0
        )
        qa_report = visual_qa.verify_task_visuals(
            task.to_dict(),
            min_video_shots=min_video_shots,
            strict_media=bool(getattr(self.settings, "visual_qa_strict", False)),
            strict_reference=bool(
                task.input.subject_reference_path
                and getattr(self.settings, "visual_qa_strict", False)
            ),
        )
        task.visual_qa = qa_report
        if getattr(self.settings, "visual_qa_enabled", True):
            visual_qa.write_report(qa_report, self.settings.media_dir / task.id / "visual_qa.json")
        if getattr(self.settings, "visual_qa_strict", False) and not qa_report["passed"]:
            raise RuntimeError("视觉验收未通过: " + "; ".join(qa_report.get("failures") or []))
        self.update(
            task,
            stage="assets",
            progress=76,
            event={
                "type": "stage",
                "stage": "assets",
                "status": "completed",
                "asset_count": len(task.assets),
                "source": ",".join(sorted(used_sources)) or asset_source,
                "video_shot_count": qa_report["requirements"]["video_shot_count"],
                "visual_qa_passed": qa_report["passed"],
            },
        )
    @staticmethod
    def _is_reusable(existing: AssetRecord, strict_dynamic: bool) -> bool:
        """已落盘的素材能不能直接复用。

        strict_dynamic（AI 图生视频）下只认视频文件：复用一张旧图会让这一镜
        退回成静止画面，和"全 AI 动态"的意图不符。
        """
        if not existing.local_path or not Path(existing.local_path).exists():
            return False
        if strict_dynamic:
            suffix = Path(existing.local_path).suffix.lower()
            return suffix in {".mp4", ".mov", ".m4v", ".webm"}
        return True

    @staticmethod
    def _reused_asset(existing: AssetRecord) -> dict[str, Any]:
        """复用已有任务素材时一并保留技术和授权来源。"""
        provenance = dict(existing.provenance or {})
        return {
            "id": existing.id,
            "name": existing.name,
            "url": existing.url,
            "local_path": existing.local_path,
            "source": existing.source,
            "license": existing.license,
            "grantor": existing.grantor,
            "authorization_ref": existing.authorization_ref,
            "selection_key": existing.selection_key, "external_id": existing.external_id, "page_url": existing.page_url, "location_evidence": existing.location_evidence,
            "search_query": existing.search_query,
            "author": existing.author,
            "usage_scope": existing.usage_scope,
            "is_ai_generated": existing.is_ai_generated,
            "media_type": existing.media_type,
            "model": existing.model,
            "seed": existing.seed,
            "prompt": existing.prompt,
            "first_frame_path": existing.first_frame_path,
            "cost": existing.cost,
            "reused": True,
            "provenance": provenance,
            "event": {"type": "asset", "provider": existing.source, **provenance},
        }

    def _plan_shot(self, task: Task, shot: Shot) -> dict[str, Any]:
        """准备好一镜生成所需的输入。放在主线程做，避免并发里读任务状态。"""
        payload = shot.__dict__.copy()
        resolved = zhejiang_kb.get_entity(payload.get("entity_id", ""), city=task.input.city) if payload.get("entity_id") else None
        if resolved is None:
            resolved = zhejiang_kb.resolve_entity(
                shot.title, shot.visual_prompt, shot.narration, city=task.input.city,
            )
        if resolved is None and not any((shot.title, shot.visual_prompt, shot.narration)):
            resolved = zhejiang_kb.resolve_entity(task.input.theme, city=task.input.city)
        payload["entity_id"] = resolved["id"] if resolved else ""
        if not payload.get("shot_tag"):
            blob = " ".join((shot.title, shot.visual_prompt)).casefold()
            tag = next((key for key, needles in {
                "process": ("制作", "工艺", "采摘", "炒制", "雕刻", "刺绣", "裁缝", "制作过程"),
                "performance": ("演出", "表演", "唱", "戏", "舞", "平调", "姚剧"),
                "detail": ("细节", "纹样", "局部", "建筑", "器物", "塔身", "桥面"),
                "ambient": ("空镜", "水面", "晨雾", "日落", "环境"),
                "wide": ("全景", "远景", "航拍", "全貌", "景色", "风光"),
            }.items() if any(needle in blob for needle in needles)), "wide")
            payload["shot_tag"] = tag
            shot.shot_tag = tag
        shot.entity_id = payload["entity_id"]
        payload.update(city=task.input.city, landmark=task.input.landmark,
                       landmark_aliases=zhejiang_kb.english_query(task.input.landmark, city=task.input.city).split(";")[:1])
        payload["library_query"] = f"{task.input.city} {task.input.theme} {shot.title}"
        # 知识库增强：中文长句映射为经核查的英文检索词，提升素材站命中率
        search_query = zhejiang_kb.english_query(
            shot.visual_prompt,
            shot.narration,
            city=task.input.city,
            landmark=task.input.landmark,
            entity_id=payload.get("entity_id", ""),
        )
        if search_query:
            payload["search_query"] = search_query
        return {"shot": shot, "payload": payload}

    def _generate_shot(
        self,
        task: Task,
        item: dict[str, Any],
        directory: Path,
        knowledge: dict[str, Any],
        style_ctx: dict[str, Any],
        candidates: tuple[str, ...],
        strict_dynamic: bool,
    ) -> dict[str, Any]:
        """在一个工作线程里跑完一镜的素材链。异常一律收进结果，不往外抛——
        一镜失败不该让其它镜头的并发任务跟着崩。"""
        shot: Shot = item["shot"]
        payload: dict[str, Any] = item["payload"]
        result: dict[str, Any] = {
            "shot_id": shot.id,
            "order": shot.order,
            "asset": None,
            "used_sources": [],
            "events": [],
            "error": "",
        }
        failures: list[str] = []
        asset: dict[str, Any] | None = None
        # strict 来源只走降级链的第一档：AI 生成失败就该报错，不该悄悄退回
        # 本地占位图，那样交付出去的"AI 成片"里会混着程序画的场景。
        chain = candidates[:1] if strict_dynamic else candidates
        try:
            for candidate in chain:
                try:
                    if candidate in FRAME_REQUIRED_PROVIDERS:
                        # 首帧按需生成：文生视频自己出画面，先做一次文生图纯属多付一次钱。
                        frame = self._ensure_first_frame(
                            task,
                            shot,
                            payload,
                            directory,
                            knowledge,
                            style_ctx,
                            allow_offline=not strict_dynamic,
                        )
                        if not frame:
                            raise RuntimeError("没有可用的首帧图片（图生视频必须有首帧）")
                        payload["first_frame_path"] = frame
                    target_dir = directory
                    if shot.subject_group and candidate in FRAME_REQUIRED_PROVIDERS and payload.get("selection_key"):
                        target_dir = directory / payload["selection_key"][:24]
                        target_dir.mkdir(parents=True, exist_ok=True)
                    with billing_scope(lambda event: self.update(task, event={**event, "shot_id": shot.id})):
                        asset = self.registry.generate_asset(candidate, task.id, payload, target_dir, style_ctx)
                    if target_dir != directory:
                        asset["url"] = self._media_url(task.id, Path(asset["local_path"]))
                    asset["selection_key"] = payload.get("selection_key","")
                except Exception as exc:
                    failures.append(f"{candidate}: {exc}")
                    continue
                result["used_sources"].append(candidate)
                if failures:
                    result["events"].append(
                        {
                            "type": "provider_fallback",
                            "slot": "asset",
                            "shot_id": shot.id,
                            "provider": candidate,
                            "fallback_reason": "; ".join(failures),
                        }
                    )
                break
            if asset is None:
                raise RuntimeError(
                    f"Shot {shot.order} 素材生成失败: {'; '.join(failures)}"
                )
            result["asset"] = asset
        except Exception as exc:  # noqa: BLE001 - 收进结果，由主线程统一决定失败
            result["error"] = str(exc)
        return result

    def _commit_shot(self, task: Task, shot: Shot, asset: dict[str, Any]) -> None:
        """把一镜的素材写回镜头和 task.assets。只在主线程按镜头顺序调用。"""
        shot.asset_url = asset["url"]
        shot.asset_id = asset["id"]
        shot.asset_local_path = asset["local_path"]
        shot.asset_media_type = str(asset.get("media_type", ""))
        shot.asset_model = str(asset.get("model", ""))
        shot.asset_seed = int(asset.get("seed", 0) or 0)
        shot.asset_first_frame_path = str(asset.get("first_frame_path", ""))
        if asset.get("provenance"):
            shot.entity_id = str(asset["provenance"].get("entity_id") or shot.entity_id)
            shot.library_asset_id = str(asset["provenance"].get("library_asset_id") or shot.library_asset_id)
            tags = asset["provenance"].get("shot_tags") or []
            if tags and shot.shot_tag not in tags:
                shot.shot_tag = str(tags[0])
        provider_event = dict(asset.get("event") or {})
        if task.trace and not asset.get("reused") and (provider_event or float(asset.get("cost", 0) or 0) > 0):
            provider_event.setdefault("type", "asset")
            provider_event.setdefault("slot", "asset")
            provider_event.setdefault("provider", asset.get("source", "unknown"))
            provider_event.setdefault("cost", float(asset.get("cost", 0) or 0))
            provider_event.setdefault("cost_reported", bool(asset.get("cost_reported", provider_event.get("cost_reported", False))))
            provider_event.setdefault("shot_id", shot.id)
            provider_event.setdefault("model", asset.get("model", ""))
            task.trace.events.append(provider_event)
        task.assets.append(
            AssetRecord(
                task_id=task.id,
                shot_id=shot.id,
                local_path=asset["local_path"],
                **{
                    key: asset[key]
                    for key in (
                        "id",
                        "name",
                        "url",
                        "source",
                        "license",
                        "author",
                        "usage_scope",
                        "is_ai_generated",
                    )
                },
                selection_key=asset.get("selection_key",""),
                grantor=asset.get("grantor", ""), authorization_ref=asset.get("authorization_ref", ""),
                external_id=asset.get("external_id",""),
                page_url=asset.get("page_url",""), location_evidence=asset.get("location_evidence",""),
                search_query=asset.get("search_query",""),
                media_type=str(asset.get("media_type", "")),
                model=str(asset.get("model", "")),
                seed=int(asset.get("seed", 0) or 0),
                prompt=str(asset.get("prompt", "")),
                first_frame_path=str(asset.get("first_frame_path", "")),
                cost=float(asset.get("cost", 0) or 0),
                provenance=dict(asset.get("provenance") or {}),
            )
        )

    def _ensure_first_frame(
        self,
        task: Task,
        shot: Shot,
        payload: dict[str, Any],
        directory: Path,
        knowledge: dict[str, Any],
        style_ctx: dict[str, Any],
        allow_offline: bool = True,
    ) -> str:
        """图生视频需要一张首帧。文生图失败时用离线场景图，不让这一镜把任务打死。"""
        existing = str(payload.get("first_frame_path") or payload.get("asset_first_frame_path") or "")
        if existing and Path(existing).is_file():
            return existing
        try:
            frame_chain = ["ai-image"] + (["offline"] if allow_offline else [])
            frame_ctx = dict(style_ctx)
            # 首帧也是画面 provider 出的，配色/风格/规格必须和后续图生视频一致。
            frame_ctx.setdefault("color", knowledge.get("color", "#2d6c63"))
            frame_ctx.setdefault("accent", knowledge.get("accent", "#d6ad60"))
            # 参考图：优先用同城市同点位的小红书采集图，让构图/机位/质感贴近实景。
            # 参考图只作为图生图的输入，不会被输出或入库——原图不会进成片。
            # Shot 上没有 spot 字段，用 entity_id 反查实体名，它与图片索引的 spot 同口径。
            entity_id = str(payload.get("entity_id") or "")
            entity = zhejiang_kb.get_entity(entity_id, city=task.input.city) if entity_id else None
            spot_name = str((entity or {}).get("name") or "")
            reference = reference_images.find_reference(
                city=task.input.city,
                spot=spot_name,
                shot_tag=str(payload.get("shot_tag") or shot.shot_tag or ""),
                seed=shot.id,
            )
            if reference:
                payload["reference_image_path"] = reference
                frame_ctx["reference_image_path"] = reference
            with billing_scope(lambda event: self.update(task, event={**event, "shot_id": shot.id, "purpose": "first_frame"})):
                acquired = self.registry.acquire_asset(
                    frame_chain,
                    lambda name, provider, body=payload, ctx=frame_ctx: invoke_create_or_find(
                        provider, task.id, body, directory, ctx
                    ),
                )
            if reference:
                self.update(
                    task,
                    event={
                        "type": "reference",
                        "shot_id": shot.id,
                        "status": "used",
                        "file": Path(reference).name,
                        "note": "reference-only：仅作图生图输入，不输出原图",
                    },
                )
        except Exception as exc:
            self._note_fallback(task, "asset", "ai-image", True, str(exc))
            return ""
        frame = str(acquired.value.get("local_path") or "")
        self._note_fallback(task, "asset", acquired.provider, acquired.fallback_used, acquired.reason)
        if frame and Path(frame).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
            return ""
        shot.asset_local_path = frame
        shot.asset_first_frame_path = frame
        self.update(task, event={"type": "first_frame", "shot_id": shot.id, "status": "ready", "provider": acquired.provider})
        return frame

    def _note_fallback(self, task: Task, slot: str, provider: str, used: bool, reason: str) -> None:
        # 并发生成时多个镜头会同时写 trace，必须加锁。
        with self._state_lock:
            if not task.trace:
                return
            if used:
                task.trace.fallback_used = True
            if not used and not reason:
                return
            task.trace.events.append(
                {
                    "type": "fallback",
                    "slot": slot,
                    "provider": provider,
                    "fallback_used": used,
                    "reason": reason,
                }
            )

    def _audio(self, task: Task) -> None:
        self.update(
            task,
            stage="audio",
            progress=48,
            event={"type": "stage", "stage": "audio", "status": "started"},
        )
        directory = self.settings.media_dir / task.id
        directory.mkdir(parents=True, exist_ok=True)
        target = float(task.input.duration)

        # P3 逐镜合成：每个镜头单独配音或使用上传音频，ffprobe 实测边界生成 cues，
        # 镜间留 150ms 静音，统一 48kHz 双声道。镜头时长由实测音频反推。
        with billing_scope(lambda event: self.update(task, event=event)):
            result = self.audio_builder.build(
                task.id,
                task.shots,
                voice=task.input.voice,
                rate=task.input.voice_rate,
                pitch=task.input.voice_pitch,
                style=task.input.voice_style,
                expressiveness=task.input.voice_expressiveness,
                force_provider="" if task.input.voice_engine == "configured" else task.input.voice_engine,
                target_duration=target,
                output_dir=directory,
            )

        # 授权曲库或明确授权的上传才可进入成片；技术音调仅限显式开启的预览。
        music_path = ""
        music_provider = "none"
        task.audio["music_notice"] = ""
        if task.input.music_mood != NO_MUSIC:
            try:
                music = select_music(
                    task_audio=task.audio,
                    mood=task.input.music_mood,
                    style=task.input.style,
                    duration=target,
                    catalog_path=self.settings.music_catalog_path,
                    music_dir=self.settings.music_dir,
                    cloud_enabled=self.settings.cloud_music_enabled,
                    output_dir=directory,
                )
                music_path = str(music.path)
                music_provider = str(getattr(music, "provider", "local-catalog"))
                result.music_path = music_path
                result.music_match = music.to_dict() if hasattr(music, "to_dict") else {}
            except Exception as music_exc:
                if not self.settings.allow_synthetic_audio_fallback or task.input.visual_usage == "publication":
                    raise RuntimeError(f"No approved music is available. Upload an authorized track or choose 无音乐. Details: {music_exc}") from music_exc
                provider = self.registry.create("music", "offline-synthetic-tone")
                music_path = str(provider.resolve(directory / "music.wav", task.input.music_mood, target))
                validate_audio_file(Path(music_path))
                music_provider = provider.name
                result.music_path = music_path
                result.music_match = {"provider": music_provider, "approved": False, "usage_scope": "technical-preview-only"}
                self._note_fallback(task, "music", music_provider, True, str(music_exc))

        event = result.event if isinstance(result.event, dict) else {}
        task.audio.update(result.to_dict())
        task.audio.update(
            {
                "voice_provider": str(event.get("voice_provider", "none")) if result.voice_path else "none",
                "voice_model": str(event.get("voice_model", "none")) if result.voice_path else "none",
                "voice_url": self._media_url(task.id, Path(result.voice_path)) if result.voice_path else "",
                "voice_local_path": result.voice_path,
                "music_provider": music_provider if music_path else "none",
                "music_url": self._media_url(task.id, Path(music_path)) if music_path else "",
                "music_local_path": music_path,
                "duration_sec": result.total_duration,
                "disclosure": "Audio provider and fallback state are recorded in trace.",
            }
        )
        if task.trace and event:
            audio_event = dict(event, stage="audio")
            if result.cost > 0:
                audio_event["cost_reported"] = True
            task.trace.events.append(audio_event)
        task.audio.pop("subtitle_cues", None)
        task.audio.pop("subtitle_alignment", None)
        align_subtitles(task.audio, self.audio_runtime,
                        sentence_only=self.settings.subtitle_alignment == "sentence")
        self._sync_shots_to_audio(task)
        # 镜头时长按 cues 实测（P1 任务3），成片容器锁回用户选择的秒数（三条不能破的指标）。
        task.audio["requested_duration"] = target
        self.update(
            task,
            stage="audio",
            progress=58,
            event={"type": "stage", "stage": "audio", "status": "completed", "duration_sec": task.audio["duration_sec"]},
        )

    @staticmethod
    def _sync_shots_to_audio(task: Task) -> None:
        """Apply P3 cue timing before any time-billed visual provider runs."""
        raw_cues = task.audio.get("cues")
        if not isinstance(raw_cues, list) or not raw_cues:
            elapsed = 0.0
            raw_cues = []
            for shot in task.shots:
                end = round(elapsed + float(shot.duration_sec), 3)
                raw_cues.append({"shot_id": shot.id, "start": elapsed, "end": end, "text": shot.subtitle or shot.narration})
                elapsed = end
            task.audio["cues"] = raw_cues
            task.audio["cue_source"] = "nominal-shot-timing"
            return

        ranges: dict[str, list[float]] = {}
        previous_end = 0.0
        for raw in raw_cues:
            shot_id = str(raw.get("shot_id", ""))
            start, end = float(raw.get("start", 0)), float(raw.get("end", 0))
            if not shot_id or start < previous_end - 0.05 or end <= start:
                raise ValueError("Audio cues must be ordered, non-overlapping, and have positive duration")
            previous_end = end
            if shot_id not in ranges:
                ranges[shot_id] = [start, end]
            else:
                ranges[shot_id][1] = end
        missing = [shot.id for shot in task.shots if shot.id not in ranges]
        if missing:
            raise ValueError(f"Audio cues are missing shots: {', '.join(missing)}")
        # 每镜的时长要一直算到**下一镜第一句**开始，把镜间停顿含进去。只取本镜
        # 首句起点到末句终点（end - start）会漏掉停顿，各镜之和就短于人声轨，
        # 画面每过一个镜头边界超前 150ms，八镜累计超过一秒。
        ordered = list(task.shots)
        for index, shot in enumerate(ordered):
            start, end = ranges[shot.id]
            if index + 1 < len(ordered):
                end = ranges[ordered[index + 1].id][0]
            shot.duration_sec = round(end - start, 3)
        task.audio["duration_sec"] = round(max(item[1] for item in ranges.values()), 3)
        task.audio["cue_source"] = "provider"

    def _media_url(self, task_id: str, path: Path) -> str:
        try:
            relative = path.resolve().relative_to((self.settings.media_dir / task_id).resolve())
        except ValueError:
            return ""
        return f"/media/{task_id}/{relative.as_posix()}"

    def _render(self, task: Task) -> None:
        render_shots = [shot.__dict__.copy() for shot in task.shots]
        for shot, render_shot in zip(task.shots, render_shots):
            record = next((asset for asset in reversed(task.assets) if asset.shot_id == shot.id), None)
            render_shot["provenance"] = dict(record.provenance or {}) if record else {}
            if task.input.visual_usage == "preview" and shot.library_asset_id:
                current = self.storage.get_library_asset(shot.library_asset_id)
                if (not current or record is None or current.get("review_status") == "rejected"
                        or current.get("license_status") == "rejected"
                        or current.get("entity_id") != shot.entity_id
                        or zhejiang_kb.canonical_city(current.get("city")) != zhejiang_kb.canonical_city(task.input.city)
                        or shot.shot_tag not in current.get("shot_tags", [])
                        or not self.storage.library_file_matches(current, Path(shot.asset_local_path))
                        or not self.storage.library_file_matches(current, Path(record.local_path))):
                    raise RuntimeError(f"No usable bound preview asset for shot {shot.order} ({shot.id})")
                render_shot["provenance"].update(library_provenance(current))
                record.provenance = render_shot["provenance"]
        if task.input.visual_usage == "publication":
            for shot, render_shot in zip(task.shots, render_shots):
                entity = zhejiang_kb.get_entity(shot.entity_id, city=task.input.city)
                if shot.entity_id and not entity:
                    raise RuntimeError(f"Shot {shot.order} ({shot.id}) publication entity does not belong to task city")
                matches = self.storage.list_library_assets(
                    limit=500, usage="publication", asset_id=shot.library_asset_id, city=task.input.city,
                ) if shot.library_asset_id else []
                record = next((asset for asset in reversed(task.assets) if asset.shot_id == shot.id), None)
                if not matches or record is None or not self.storage.library_file_matches(matches[0], Path(shot.asset_local_path)):
                    identifier = shot.library_asset_id or "unbound"
                    raise RuntimeError(f"No approved publication asset for shot {shot.order} ({shot.id}); library_asset_id={identifier}")
                current = matches[0]
                if not self.storage.library_file_matches(current, Path(record.local_path)) or record.provenance.get("library_asset_id") != current["id"]:
                    raise RuntimeError(f"Shot {shot.order} ({shot.id}) publication asset record does not match its file")
                if current.get("entity_id") != shot.entity_id or shot.shot_tag not in (current.get("shot_tags") or []):
                    raise RuntimeError(f"Shot {shot.order} ({shot.id}) publication asset does not match its entity or shot tag")
                provenance = {**dict(record.provenance or {}), **library_provenance(current)}
                record.provenance = provenance
                render_shot["provenance"] = provenance
                render_shot["library_asset_id"] = current["id"]
                render_shot["asset_media_type"] = current["asset_type"]
                render_shot["entity_id"] = shot.entity_id
                render_shot["shot_tag"] = shot.shot_tag
        self.update(
            task,
            stage="rendering",
            progress=84,
            event={"type": "stage", "stage": "rendering", "status": "started"},
        )
        renderer = self.registry.create("render", self.settings.render_provider)
        # 成片沿用任务所选时长、画幅和帧率。
        task.audio["requested_duration"] = float(task.input.duration)
        # 旁白出现时按任务所选模式调整背景音乐。
        task.audio["duck"] = {
            "mode": task.input.music_ducking,
            "release_sec": 0.35,
        }
        grade = task.audio.get("grade") if isinstance(task.audio.get("grade"), dict) else None
        task.render = renderer.render(
            task.id, render_shots, task.audio, task.input.include_ai_label,
            task.input.include_subtitles, task.input.output_resolution, task.input.fps,
            task.input.voice_volume, task.input.music_volume, grade,
        )
        try:
            review = build_review(task.to_dict(), self.settings.media_dir / task.id)
            if task.trace:
                task.trace.events.append({"type": "visual_review", "shot_count": len(review["shots"])})
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if task.trace:
                task.trace.events.append({"type": "visual_review", "error": str(exc)})
        self.update(
            task,
            stage="rendering",
            progress=96,
            event={
                "type": "stage", "stage": "rendering", "status": "completed",
                "video_url": task.render.get("video_url", ""),
            },
        )

    def update_shot(self, task_id: str, shot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        task = self._load(task_id)
        if task.status not in ("draft", "edited", "failed"):
            raise ValueError("Only a draft task can be edited")
        original_duration = next((shot.duration_sec for shot in task.shots if shot.id == shot_id), None)
        self._patch_shot(task, shot_id, patch)
        shot = next(item for item in task.shots if item.id == shot_id)
        if original_duration != shot.duration_sec and abs(sum(item.duration_sec for item in task.shots) - task.input.duration) > 0.01:
            raise ValueError(f"Shot durations must add up to the task duration ({task.input.duration}s)")
        task.status = "draft"
        task.stage = "preview"
        task.progress = 35
        task.error = ""
        task.updated_at = now_iso()
        self.storage.save_task(task)
        return task.to_dict()

    def _patch_shot(self, task: Task, shot_id: str, patch: dict[str, Any]) -> None:
        shot = next((item for item in task.shots if item.id == shot_id), None)
        if not shot:
            raise KeyError("Shot does not exist")
        original_duration = shot.duration_sec
        if "subject_group" in patch:
            shot.subject_group = str(patch["subject_group"]).strip()
            if len(shot.subject_group)>80:
                raise ValueError("主体组名不能超过 80 字")
        if "subject_reference" in patch:
            key = str(patch["subject_reference"]).strip()
            if key:
                reference_path(self.settings.media_dir/task.id/"references",key)
            shot.subject_reference = key
        if patch.get("clear_audio") is True:
            shot.audio_local_path = ""
            shot.audio_url = ""
        if "entity_id" in patch:
            entity_id = str(patch["entity_id"] or "").strip()
            entity = zhejiang_kb.get_entity(entity_id, task.input.city) if entity_id else None
            if entity_id and not entity:
                raise ValueError("entity_id is not in the selected city knowledge base")
            shot.entity_id = entity_id
        if "shot_tag" in patch:
            tag = str(patch["shot_tag"] or "").strip()
            if tag and tag not in SHOT_TAGS:
                raise ValueError("shot_tag is invalid")
            shot.shot_tag = tag
        if any(key in patch for key in ("entity_id", "shot_tag", "library_asset_id")):
            asset_id = str(patch.get("library_asset_id", shot.library_asset_id) or "").strip()
            library_asset = self.storage.get_library_asset(asset_id) if asset_id else None
            if asset_id and not library_asset:
                raise ValueError("library_asset_id does not exist")
            if library_asset:
                if library_asset.get("entity_id") != shot.entity_id:
                    raise ValueError("library asset does not match this shot entity")
                if shot.shot_tag not in (library_asset.get("shot_tags") or []):
                    raise ValueError("library asset does not match this shot tag")
                if library_asset.get("asset_type") not in {"image", "video"} or library_asset.get("review_status") == "rejected":
                    raise ValueError("library asset is not usable")
                if not self.storage.library_file_matches(library_asset, Path(library_asset["local_path"])):
                    raise ValueError("library asset file has changed")
            if library_asset or shot.library_asset_id:
                task.assets = [asset for asset in task.assets if asset.shot_id != shot.id]
                shot.asset_id = ""
                shot.asset_url = ""
                shot.asset_local_path = ""
                shot.asset_media_type = ""
                shot.asset_model = ""
                shot.asset_seed = 0
                shot.asset_first_frame_path = ""
            shot.library_asset_id = asset_id
            if library_asset:
                record = library_asset_record(task.id, shot.id, library_asset)
                task.assets.append(record)
                shot.asset_id = record.id
                shot.asset_url = record.url
                shot.asset_local_path = record.local_path
                shot.asset_media_type = record.media_type
        for key, value in validate_clip({**vars(shot), **patch}).items():
            setattr(shot, key, value)
        for key in ("title", "visual_prompt", "subtitle", "narration", "transition"):
            if key in patch:
                value = str(patch[key]).strip()
                if not value or len(value) > 1000:
                    raise ValueError(f"{key} cannot be empty or longer than 1000 characters")
                setattr(shot, key, value)
        if shot.transition not in TRANSITIONS:
            raise ValueError(f"transition must be one of {TRANSITIONS}")
        if "motion" in patch:
            motion = str(patch["motion"] or "auto").strip().lower()
            if motion not in {"auto", "push", "pull", "gentle"}:
                raise ValueError("motion must be auto, push, pull, or gentle")
            shot.motion = motion
        if "focus_x" in patch or "focus_y" in patch:
            coordinates = []
            for key in ("focus_x", "focus_y"):
                raw = patch.get(key, getattr(shot, key))
                if raw in (None, ""):
                    coordinates.append(None)
                else:
                    value = float(raw)
                    if not math.isfinite(value) or not 0 <= value <= 1:
                        raise ValueError(f"{key} must be between 0 and 1")
                    coordinates.append(value)
            if (coordinates[0] is None) != (coordinates[1] is None):
                raise ValueError("focus_x and focus_y must both be set or both be empty")
            shot.focus_x, shot.focus_y = coordinates
        if "duration_sec" in patch:
            duration = float(patch["duration_sec"])
            if not math.isfinite(duration) or duration <= 0 or (duration != original_duration and not 1 <= duration <= 20):
                raise ValueError("Changed duration_sec must be between 1 and 20")
            shot.duration_sec = duration
