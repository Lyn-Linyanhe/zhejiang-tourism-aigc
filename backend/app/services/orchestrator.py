from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import AssetRecord, PIPELINE_STAGES, Shot, Task, TaskInput, TraceRecord, now_iso, new_id
from ..providers.contract import AudioResult, Cue
from ..providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from ..providers.media_ffmpeg import generate_tone, validate_audio_file
from ..providers.moneyprinterturbo import MoneyPrinterTurboLLMProvider
from ..providers.music import find_music_file
from ..providers.offline import OfflineLLMProvider
from ..providers.registry import ProviderRegistry
from ..providers.tts import build_audio
from ..services import consistency, cost, knowledge as zhejiang_kb
from ..services.renderer import VideoRenderer
from ..storage import Storage


NO_VOICE = "\u65e0\u914d\u97f3"
NO_MUSIC = "\u65e0\u97f3\u4e50"
FEMALE_VOICE = "\u5973\u58f0"
MOOD_FREQUENCIES = {
    "\u8212\u7f13": 220,
    "\u70ed\u70c8": 330,
    "\u96c5\u81f4": 260,
    "\u8f7b\u5feb": 440,
}


class Orchestrator:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.renderer = VideoRenderer(settings.media_dir)
        self.registry = ProviderRegistry(settings)
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.RLock()

    def create_task(self, task_input: TaskInput) -> Task:
        task = Task(id=new_id("task"), input=task_input)
        task.trace = TraceRecord(
            provider="pending",
            model="pending",
            prompt_version="zj-tourism-prompt-v1",
            fallback_used=False,
        )
        self.storage.save_task(task)
        self.start(task.id)
        return task

    def create_preview(self, task_input: TaskInput) -> Task:
        task = Task(id=new_id("draft"), input=task_input, status="draft", stage="preview", progress=5)
        task.trace = TraceRecord(
            provider="pending",
            model="pending",
            prompt_version="zj-tourism-prompt-v1",
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
        self.start(task_id, "storyboard")
        return {"task_id": task_id, "accepted": True, "from_stage": "storyboard"}

    def update_draft(self, task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        task = self._load(task_id)
        if task.status not in ("draft", "edited", "failed"):
            raise ValueError("Only a draft task can be edited")
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
        task.status = "draft"
        task.stage = "preview"
        task.progress = 35
        task.error = ""
        self.update(task, event={"type": "preview", "status": "edited"})
        return task.to_dict()

    def start(self, task_id: str, from_stage: str | None = None) -> None:
        with self._threads_lock:
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
        if from_stage not in PIPELINE_STAGES:
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
        task = Task(
            id=data["id"],
            input=TaskInput.from_dict(data["input"]),
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
            error=data.get("error", ""),
            created_at=data.get("created_at", now_iso()),
            updated_at=data.get("updated_at", now_iso()),
        )
        task.shots = [
            Shot(**{key: value for key, value in shot.items() if key in Shot.__dataclass_fields__})
            for shot in data.get("shots", [])
        ]
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
        try:
            self.update(task, status="running", stage=from_stage or "scripting", progress=5)
            # v1：先配音，再按真实时长出画面。画面按秒计费，不能先生成再被语速打废。
            if from_stage in (None, "scripting"):
                self._scripting(task)
                from_stage = "storyboard"
            if from_stage in (None, "storyboard", "scripting"):
                self._storyboard(task)
                from_stage = "audio"
            if from_stage in (None, "audio", "storyboard", "scripting"):
                self._audio(task)
                from_stage = "assets"
            if from_stage in (None, "assets", "audio", "storyboard", "scripting"):
                self._assets(task)
                from_stage = "rendering"
            if from_stage in (None, "rendering", "assets", "audio", "storyboard", "scripting"):
                self._render(task)
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

    def _scripting(self, task: Task) -> None:
        self.update(
            task,
            stage="scripting",
            progress=12,
            event={"type": "stage", "stage": "scripting", "status": "started"},
        )
        # 只把创作相关信息喂给 LLM。渲染/工程参数（fps、分辨率、音量、是否烧字幕
        # 等）对写脚本毫无价值，却会稀释知识参考块的权重。
        creative_fields = (
            "city", "landmark", "theme", "culture", "festival",
            "audience", "style", "duration", "shot_count", "custom_brief",
        )
        creative_input = {
            key: getattr(task.input, key, "")
            for key in creative_fields
            if getattr(task.input, key, "") not in ("", None)
        }
        prompt = json.dumps(creative_input, ensure_ascii=False, sort_keys=True)
        provider: LLMProvider
        selected = self.settings.default_provider.lower()
        if selected in {"mpt", "moneyprinterturbo"}:
            provider = MoneyPrinterTurboLLMProvider(
                self.settings.mpt_root,
                self.settings.mpt_timeout_seconds,
                self.settings.mpt_python,
            )
        elif self.settings.llm_base_url and self.settings.llm_api_key:
            provider = OpenAICompatibleLLMProvider(
                self.settings.llm_base_url,
                self.settings.llm_api_key,
                self.settings.llm_model,
                self.settings.llm_timeout_seconds,
            )
        else:
            provider = OfflineLLMProvider()
        try:
            result = provider.generate(prompt, task.input.__dict__)
        except Exception as exc:
            if not self.settings.allow_provider_fallback and selected != "offline":
                raise RuntimeError(f"{self.settings.default_provider} provider failed: {exc}") from exc
            result = OfflineLLMProvider().generate(prompt, task.input.__dict__)
            result.event["fallback_reason"] = str(exc)
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
        task.trace = TraceRecord(
            provider=result.provider,
            model=result.model,
            prompt_version=result.event.get("prompt_version", "zj-tourism-prompt-v1"),
            fallback_used=result.fallback_used,
            events=[result.event],
        )
        task.audio["knowledge"] = result.data.get("knowledge", {})
        self.update(
            task,
            stage="scripting",
            progress=27,
            event={"type": "stage", "stage": "scripting", "status": "completed"},
        )

    @staticmethod
    def _normalize_shots(shots: list[dict[str, Any]], duration: int) -> list[dict[str, Any]]:
        """脚本阶段只保留相对比例，不把镜头时长锁死成输入秒数。

        配音完成后由 apply_audio_durations() 按 cues 反推真实时长。
        """
        if not shots:
            raise ValueError("Provider returned no shots")
        weights = [max(0.1, float(item.get("duration_sec", 0) or 0.1)) for item in shots]
        total = sum(weights) or float(len(shots))
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(shots, start=1):
            copy = dict(item)
            copy["id"] = str(copy.get("id") or f"shot_{index}")
            copy["order"] = index
            copy["duration_sec"] = round(max(1.0, float(duration) * weights[index - 1] / total), 2)
            normalized.append(copy)
        return normalized

    @staticmethod
    def apply_audio_durations(task: Task, cues: list[dict[str, Any]], measured: float) -> float:
        """镜头时长等于用户选择的秒数，句间停顿留在画面里。

        成片必须等于 input.duration。配音可以短于画面；配音长于画面时，
        超出的部分由渲染裁掉，不把画面拉长。有匹配 cue 时，每个镜头吃进
        自己的说话跨度，再吃进和上一镜之间的空隙，空隙不能从总和里丢掉。
        """
        if not task.shots:
            raise ValueError("The draft has no storyboard")
        requested = float(task.input.duration)
        voice_end = measured if measured > 0 else 0.0
        for raw in cues:
            try:
                voice_end = max(voice_end, float(raw.get("end", 0) or 0))
            except (TypeError, ValueError):
                continue
        # 画面只跟用户选择的秒数走。配音更长时裁配音，不把 target 抬到配音结尾。
        target = requested if requested > 0 else voice_end

        by_shot: dict[str, tuple[float, float]] = {}
        for raw in cues:
            shot_id = str(raw.get("shot_id") or "")
            if not shot_id:
                continue
            try:
                start = float(raw.get("start", 0))
                end = float(raw.get("end", start))
            except (TypeError, ValueError):
                continue
            if shot_id not in by_shot:
                by_shot[shot_id] = (start, end)
            else:
                previous_start, previous_end = by_shot[shot_id]
                by_shot[shot_id] = (min(previous_start, start), max(previous_end, end))

        ordered = [shot for shot in task.shots if shot.id in by_shot]
        if ordered:
            # 画面时间轴从 0 起：第一镜含片头静音，之后每镜含与上一镜之间的停顿。
            # 最后一镜铺到用户选择的秒数；配音超出时在这里截断，不把成片拉长。
            # 右边界用下一句的开始，不用本句的结束。否则句间停顿会被丢掉，
            # 画面总和短于配音绝对时间，最后一句字幕就会落在画面外面。
            boundaries = [0.0]
            for index, shot in enumerate(ordered):
                _start, end = by_shot[shot.id]
                if index + 1 < len(ordered):
                    next_start, _next_end = by_shot[ordered[index + 1].id]
                    end = next_start
                else:
                    end = target
                boundaries.append(min(target, max(boundaries[-1], end)))
            assigned = [round(max(0.0, end - start), 3) for start, end in zip(boundaries, boundaries[1:])]
            # 四舍五入可能把总和偏出请求秒数。差额补进最后一镜，再按比例收回来，
            # 保证返回值等于用户选择的秒数，而不是配音结尾。
            drift = round(target - sum(assigned), 3)
            if ordered and abs(drift) >= 0.001:
                assigned[-1] = round(assigned[-1] + drift, 3)
            if ordered and assigned[-1] < 0:
                scale = target / sum(item for item in assigned if item > 0) if any(item > 0 for item in assigned) else 0
                assigned = [round(max(0.0, item * scale), 3) for item in assigned]
                assigned[-1] = round(assigned[-1] + (target - sum(assigned)), 3)
            for shot, duration in zip(ordered, assigned):
                shot.duration_sec = round(max(0.0, duration), 3)
            # 没有 cue 的镜头不在配音时间轴上。时长置 0，渲染时跳过，
            # 避免把脚本阶段的名义时长再加一遍，把成片拉得比请求秒数更长。
            for shot in task.shots:
                if shot.id not in by_shot:
                    shot.duration_sec = 0.0
            covered = round(sum(float(shot.duration_sec) for shot in ordered), 3)
            if ordered and abs(covered - target) >= 0.001:
                ordered[-1].duration_sec = round(float(ordered[-1].duration_sec) + (target - covered), 3)
            return round(sum(float(shot.duration_sec) for shot in task.shots), 3)

        spoken = target
        weights = [max(0.1, float(shot.duration_sec)) for shot in task.shots]
        weight_total = sum(weights) or float(len(task.shots))
        for shot, weight in zip(task.shots, weights):
            shot.duration_sec = round(max(0.04, spoken * weight / weight_total), 3)
        drift = round(spoken - sum(float(shot.duration_sec) for shot in task.shots), 3)
        if task.shots and abs(drift) >= 0.001:
            task.shots[-1].duration_sec = round(float(task.shots[-1].duration_sec) + drift, 3)
        return round(sum(float(shot.duration_sec) for shot in task.shots), 3)

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
            progress=62,
            event={"type": "stage", "stage": "assets", "status": "started"},
        )
        directory = self.settings.media_dir / task.id / "assets"
        knowledge = task.audio.get("knowledge", {})
        style_ctx = self._style_ctx(task, knowledge)
        task.audio["grade"] = style_ctx["grade"]
        costs: list[float] = []
        asset_source = (
            self.settings.asset_provider
            if task.input.asset_source == "configured"
            else task.input.asset_source
        )
        chain = self.registry.asset_chain_for(asset_source)
        self.registry.bind_library(lambda shot: self._library_asset(task, shot))
        existing_assets = {asset.shot_id: asset for asset in task.assets}
        task.assets = []
        for shot in task.shots:
            existing = existing_assets.get(shot.id)
            if existing and existing.local_path and Path(existing.local_path).exists():
                asset = {
                    "id": existing.id,
                    "name": existing.name,
                    "url": existing.url,
                    "local_path": existing.local_path,
                    "source": existing.source,
                    "license": existing.license,
                    "author": existing.author,
                    "usage_scope": existing.usage_scope,
                    "is_ai_generated": existing.is_ai_generated,
                }
            elif chain == ["upload"]:
                raise RuntimeError(
                    f"Shot {shot.order} has no uploaded image/video asset. "
                    "Upload an asset for every shot or select Pexels/offline assets."
                )
            else:
                shot_payload = self._shot_payload(task, shot)
                # 只有画面链路里真有图生视频时才准备首帧。offline / pexels /
                # upload / local-library 不请求文生图，避免 trace 里出现
                # 「未配置 IMAGE_BASE_URL」。
                if "cloud-i2v" in chain:
                    frame = self._ensure_first_frame(task, shot, shot_payload, directory, knowledge, style_ctx)
                    if frame:
                        shot_payload["first_frame_path"] = frame
                acquired = self.registry.acquire_asset(
                    chain,
                    lambda provider, payload=shot_payload: provider.create_or_find(
                        task.id,
                        payload,
                        directory,
                        knowledge.get("color", "#2d6c63"),
                        knowledge.get("accent", "#d6ad60"),
                        style_ctx,
                    ),
                )
                asset = acquired.value
                costs.append(float(asset.get("cost") or 0))
                self._note_fallback(task, "asset", acquired.provider, acquired.fallback_used, acquired.reason)
            shot.asset_url = asset["url"]
            shot.asset_id = asset["id"]
            shot.asset_local_path = asset["local_path"]
            media_type = str(asset.get("media_type") or "")
            if not media_type:
                suffix = Path(str(asset["local_path"])).suffix.lower()
                media_type = "video" if suffix in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"} else "image"
            shot.asset_media_type = media_type
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
                )
            )
        self.update(
            task,
            stage="assets",
            progress=78,
            event={
                "type": "stage",
                "stage": "assets",
                "status": "completed",
                "asset_count": len(task.assets),
                "source": asset_source,
                "chain": chain,
                "cost": cost.sum_costs(costs),
            },
        )
        task.audio["cost"] = cost.sum_costs(costs)

    def _shot_payload(self, task: Task, shot: Shot) -> dict[str, Any]:
        # 知识库增强：把镜头的画面描述/旁白映射为经核查的英文检索词。
        # 中文长句直接投喂 Pexels 等国际素材站命中率极低。
        shot_payload = dict(shot.__dict__)
        search_query = zhejiang_kb.english_query(
            shot_payload.get("visual_prompt", ""),
            shot_payload.get("narration", ""),
            task.input.theme,
            city=task.input.city,
            landmark=task.input.landmark,
        )
        if search_query:
            shot_payload["search_query"] = search_query
        shot_payload["task_id"] = task.id
        return shot_payload

    def _style_ctx(self, task: Task, knowledge: dict[str, Any]) -> dict[str, Any]:
        ctx = {
            "task_id": task.id,
            "style": task.input.style,
            "color": knowledge.get("color", "#2d6c63"),
            "accent": knowledge.get("accent", "#d6ad60"),
            "resolution": task.input.output_resolution,
        }
        ctx["grade"] = consistency.grade(ctx)
        return ctx

    def _ensure_first_frame(
        self,
        task: Task,
        shot: Shot,
        payload: dict[str, Any],
        directory: Path,
        knowledge: dict[str, Any],
        style_ctx: dict[str, Any],
    ) -> str:
        """图生视频需要一张首帧。文生图失败时用离线场景图，不让这一镜把任务打死。"""
        existing = str(payload.get("first_frame_path") or "")
        if existing and Path(existing).is_file():
            return existing
        try:
            acquired = self.registry.acquire_asset(
                ["cloud-t2i", "offline"],
                lambda provider, body=payload: provider.create_or_find(
                    task.id,
                    body,
                    directory,
                    knowledge.get("color", "#2d6c63"),
                    knowledge.get("accent", "#d6ad60"),
                    style_ctx,
                ),
            )
        except Exception as exc:
            self._note_fallback(task, "asset", "cloud-t2i", True, str(exc))
            return ""
        frame = str(acquired.value.get("local_path") or "")
        self._note_fallback(task, "asset", acquired.provider, acquired.fallback_used, acquired.reason)
        if frame and Path(frame).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
            return ""
        shot.asset_local_path = frame
        return frame

    def _library_asset(self, task: Task, shot: dict[str, Any]) -> dict[str, Any] | None:
        query = f"{task.input.city} {task.input.theme} {shot.get('title', '')}"
        library_asset = self.storage.find_library_asset("image", query)
        if not library_asset:
            library_asset = self.storage.find_library_asset("video", query)
        if not library_asset:
            library_asset = self.storage.find_library_asset("image") or self.storage.find_library_asset("video")
        if not library_asset or not Path(str(library_asset.get("local_path", ""))).is_file():
            return None
        return {
            "id": library_asset["id"],
            "name": library_asset.get("name", shot.get("title", "")),
            "url": library_asset.get("url", ""),
            "local_path": library_asset["local_path"],
            "source": "local-library",
            "license": library_asset.get("license_status", "pending-review"),
            "author": library_asset.get("author", "unknown"),
            "usage_scope": library_asset.get("usage_scope", "technical-validation"),
            "is_ai_generated": bool(library_asset.get("is_ai_generated", False)),
        }

    def _note_fallback(self, task: Task, slot: str, provider: str, used: bool, reason: str) -> None:
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
        nominal = sum(float(shot.duration_sec) for shot in task.shots) or float(task.input.duration)
        voice_path: Path | None = directory / "voice.wav"
        music_path: Path | None = directory / "music.wav"
        tts_provider = "none"
        tts_model = "none"
        cues: list[dict[str, Any]] = []

        uploaded_voice_value = str(task.audio.get("voice_local_path", "")).strip()
        uploaded_voice = Path(uploaded_voice_value) if uploaded_voice_value else None
        audio_event: dict[str, Any] = {}
        if task.input.voice == NO_VOICE:
            voice_path = None
        elif uploaded_voice and uploaded_voice.is_file():
            voice_path = uploaded_voice
            validate_audio_file(voice_path)
            tts_provider = "local-upload"
            tts_model = "uploaded-audio"
        else:
            produced = self._speak(task, directory)
            voice_path = Path(produced.voice_path) if produced.voice_path else None
            if voice_path:
                validate_audio_file(voice_path)
            tts_provider = str(produced.event.get("provider") or "edge-tts")
            tts_model = str(produced.event.get("model") or "edge-tts")
            cues = [
                {"shot_id": cue.shot_id, "start": cue.start, "end": cue.end, "text": cue.text}
                for cue in produced.cues
            ]
            audio_event = dict(produced.event)
            if produced.music_path:
                music_path = Path(produced.music_path)
            if task.trace:
                task.trace.events.append(audio_event)
                if audio_event.get("estimated"):
                    task.trace.fallback_used = True

        measured = self._probe_duration(voice_path) if voice_path else 0.0
        if voice_path and not cues:
            cues = self._estimate_cues(task, measured or nominal)
        total = self.apply_audio_durations(task, cues, measured or nominal)

        if task.input.music_mood == NO_MUSIC:
            music_path = None
        else:
            uploaded_music_value = str(task.audio.get("music_local_path", "")).strip()
            uploaded_music = Path(uploaded_music_value) if uploaded_music_value else None
            configured_music = find_music_file(self.settings.music_dir, task.input.music_mood)
            music_path = (
                uploaded_music
                if uploaded_music and uploaded_music.is_file()
                else configured_music
            )
            if music_path:
                validate_audio_file(music_path)
                music_provider = (
                    "local-upload"
                    if uploaded_music and uploaded_music.is_file()
                    else "local-music-library"
                )
            elif self.settings.allow_synthetic_audio_fallback:
                music_path = directory / "music.wav"
                generate_tone(
                    music_path,
                    total,
                    MOOD_FREQUENCIES.get(task.input.music_mood, 220),
                    0.08,
                )
                music_provider = "offline-synthetic-tone"
            else:
                raise RuntimeError(
                    "Music was requested but no real music file is configured. "
                    "Upload a music track, place one in MUSIC_DIR, or choose 无音乐."
                )

        task.audio.update(
            {
                "voice_provider": tts_provider if voice_path else "none",
                "voice_model": tts_model if voice_path else "none",
                "voice_url": self._media_url(task.id, voice_path) if voice_path else "",
                "voice_local_path": str(voice_path) if voice_path else "",
                "music_provider": locals().get("music_provider", "none") if music_path else "none",
                "music_file": Path(music_path).name if music_path else "",
                "music_url": self._media_url(task.id, music_path) if music_path else "",
                "music_local_path": str(music_path) if music_path else "",
                "duration_sec": total,
                "measured_voice_sec": measured,
                "cues": cues,
                "event": audio_event,
                "duck": audio_event.get("mix") if isinstance(audio_event.get("mix"), dict) else {},
                "disclosure": "Audio provider and fallback state are recorded in trace.",
            }
        )
        self.update(
            task,
            stage="audio",
            progress=58,
            event={"type": "stage", "stage": "audio", "status": "completed", "duration_sec": total},
        )

    def _speak(self, task: Task, directory: Path) -> AudioResult:
        """先用声音线的 AudioResult。edge 和系统语音都失败时，才回到注册表。"""
        shots = [shot.__dict__.copy() for shot in task.shots]
        try:
            return build_audio(
                task.narration,
                task.input.voice,
                task.input.voice_rate,
                task.input.music_mood,
                shots,
                directory,
                self.settings.music_dir,
            )
        except Exception as exc:
            self._note_fallback(task, "audio", "edge-tts", True, str(exc))
        nominal = sum(float(shot.duration_sec) for shot in task.shots) or float(task.input.duration)
        target = directory / "voice.wav"
        acquired = self.registry.acquire_audio(
            self.registry.audio_chain(),
            lambda provider, path=target: provider.synthesize(
                task.narration,
                path,
                task.input.voice,
                task.input.voice_rate,
                duration=nominal,
                frequency=520 if task.input.voice == FEMALE_VOICE else 360,
                shots=shots,
            ),
        )
        payload = acquired.value
        self._note_fallback(task, "audio", acquired.provider, acquired.fallback_used, acquired.reason)
        cues = self._cues_from_payload(payload, task)
        voice = str(payload.get("path") or target)
        duration = self._probe_duration(Path(voice)) or nominal
        result = AudioResult(
            voice_path=voice,
            music_path="",
            cues=[Cue(**item) for item in cues] if cues else self._cue_objects(task, duration),
            total_duration=duration,
            event=dict(payload.get("event") or {"type": "tts", "provider": acquired.provider, "estimated": not cues}),
        )
        result.validate()
        return result

    @staticmethod
    def _cue_objects(task: Task, measured: float) -> list[Cue]:
        return [Cue(**item) for item in Orchestrator._estimate_cues(task, measured)]

    @staticmethod
    def _cues_from_payload(payload: dict[str, Any], task: Task) -> list[dict[str, Any]]:
        raw = payload.get("cues") or payload.get("audio_result") or []
        if isinstance(raw, AudioResult):
            raw.validate()
            return [
                {"shot_id": cue.shot_id, "start": cue.start, "end": cue.end, "text": cue.text}
                for cue in raw.cues
            ]
        if isinstance(raw, dict) and raw.get("cues"):
            parsed = AudioResult.from_dict(raw)
            parsed.validate()
            return [
                {"shot_id": cue.shot_id, "start": cue.start, "end": cue.end, "text": cue.text}
                for cue in parsed.cues
            ]
        if not isinstance(raw, list) or not raw:
            return []
        cues = [
            item if isinstance(item, Cue) else Cue(
                shot_id=str(item.get("shot_id", "")),
                start=float(item.get("start", 0)),
                end=float(item.get("end", 0)),
                text=str(item.get("text", "")),
            )
            for item in raw
        ]
        AudioResult(
            voice_path=str(payload.get("path") or "voice.wav"),
            music_path="",
            cues=cues,
            total_duration=max(cue.end for cue in cues),
        ).validate()
        known = {shot.id for shot in task.shots}
        return [
            {"shot_id": cue.shot_id, "start": cue.start, "end": cue.end, "text": cue.text}
            for cue in cues
            if cue.shot_id in known
        ]

    @staticmethod
    def _estimate_cues(task: Task, measured: float) -> list[dict[str, Any]]:
        """现有 TTS 不回时间戳时，按各镜头旁白字数把实测时长切开。"""
        weights = [max(1, len((shot.narration or shot.subtitle or "").strip())) for shot in task.shots]
        total_weight = sum(weights) or len(task.shots)
        elapsed = 0.0
        cues: list[dict[str, Any]] = []
        for index, shot in enumerate(task.shots):
            if index == len(task.shots) - 1:
                end = round(measured, 3)
            else:
                end = round(elapsed + measured * weights[index] / total_weight, 3)
            if end <= elapsed:
                end = round(elapsed + 0.05, 3)
            cues.append(
                {
                    "shot_id": shot.id,
                    "start": round(elapsed, 3),
                    "end": end,
                    "text": shot.narration or shot.subtitle,
                }
            )
            elapsed = end
        return cues

    @staticmethod
    def _probe_duration(path: Path) -> float:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            return max(0.0, float((result.stdout or "").strip()))
        except ValueError:
            return 0.0

    def _media_url(self, task_id: str, path: Path) -> str:
        try:
            relative = path.resolve().relative_to((self.settings.media_dir / task_id).resolve())
        except ValueError:
            return ""
        return f"/media/{task_id}/{relative.as_posix()}"

    def _render(self, task: Task) -> None:
        self.update(
            task,
            stage="rendering",
            progress=86,
            event={"type": "stage", "stage": "rendering", "status": "started"},
        )
        render_shots = [shot.__dict__.copy() for shot in task.shots]
        grade = task.audio.get("grade") if isinstance(task.audio.get("grade"), dict) else {}
        # 渲染只认这个秒数裁画面和配音，不再用配音结尾把成片拉长。
        task.audio["requested_duration"] = float(task.input.duration)
        task.render = self.renderer.render(
            task.id,
            render_shots,
            task.audio,
            task.input.include_ai_label,
            task.input.include_subtitles,
            task.input.output_resolution,
            task.input.fps,
            task.input.voice_volume,
            task.input.music_volume,
            grade,
        )
        self.update(
            task,
            stage="rendering",
            progress=96,
            event={
                "type": "stage",
                "stage": "rendering",
                "status": "completed",
                "video_url": task.render.get("video_url", ""),
            },
        )

    def update_shot(self, task_id: str, shot_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        task = self._load(task_id)
        if task.status not in ("draft", "edited", "failed"):
            raise ValueError("Only a draft task can be edited")
        shot = next((item for item in task.shots if item.id == shot_id), None)
        if not shot:
            raise KeyError("Shot does not exist")
        for key in ("title", "visual_prompt", "subtitle", "narration", "transition"):
            if key in patch:
                value = str(patch[key]).strip()
                if not value or len(value) > 1000:
                    raise ValueError(f"{key} cannot be empty or longer than 1000 characters")
                setattr(shot, key, value)
        if "duration_sec" in patch:
            # 草稿阶段还没有配音。这里只改相对时长，不再要求总和等于输入秒数。
            duration = float(patch["duration_sec"])
            if not 1 <= duration <= 20:
                raise ValueError("duration_sec must be between 1 and 20")
            shot.duration_sec = duration
        task.status = "draft"
        task.stage = "preview"
        task.progress = 35
        task.error = ""
        task.updated_at = now_iso()
        self.storage.save_task(task)
        return task.to_dict()
