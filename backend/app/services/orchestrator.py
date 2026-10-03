from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..models import AssetRecord, Shot, Task, TaskInput, TraceRecord, now_iso, new_id
from ..providers.media_ffmpeg import validate_audio_file
from ..providers.registry import ProviderRegistry, build_provider_registry
from ..storage import Storage


NO_VOICE = "\u65e0\u914d\u97f3"
NO_MUSIC = "\u65e0\u97f3\u4e50"

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
        self._threads: dict[str, threading.Thread] = {}
        self._threads_lock = threading.RLock()

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
            selected_stage = from_stage or PIPELINE[0].name
            stage_names = [stage.name for stage in PIPELINE]
            if selected_stage not in stage_names:
                raise ValueError(f"Unsupported pipeline stage: {selected_stage}")
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
        asset_source = (
            self.settings.asset_provider
            if task.input.asset_source == "configured"
            else task.input.asset_source
        )
        candidates = (
            self.registry.candidates("asset", asset_source)
            if task.input.asset_source == "configured"
            else (self.registry.resolve_name("asset", asset_source),)
        )
        existing_assets = {asset.shot_id: asset for asset in task.assets}
        task.assets = []
        used_sources: set[str] = set()
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
            else:
                shot_payload = shot.__dict__.copy()
                shot_payload["library_query"] = f"{task.input.city} {task.input.theme} {shot.title}"
                failures: list[str] = []
                asset = None
                for candidate in candidates:
                    try:
                        asset = self.registry.generate_asset(
                            candidate,
                            task.id,
                            shot_payload,
                            directory,
                            {
                                "color": knowledge.get("color", "#2d6c63"),
                                "accent": knowledge.get("accent", "#d6ad60"),
                            },
                        )
                        used_sources.add(candidate)
                        if failures and task.trace:
                            task.trace.fallback_used = True
                            task.trace.events.append({
                                "type": "provider_fallback", "slot": "asset",
                                "shot_id": shot.id, "provider": candidate,
                                "fallback_reason": "; ".join(failures),
                            })
                        break
                    except Exception as exc:
                        failures.append(f"{candidate}: {exc}")
                if asset is None:
                    raise RuntimeError(f"No asset provider succeeded for shot {shot.order}: {'; '.join(failures)}")
            shot.asset_url = asset["url"]
            shot.asset_id = asset["id"]
            shot.asset_local_path = asset["local_path"]
            shot.asset_media_type = str(asset.get("media_type", ""))
            shot.asset_model = str(asset.get("model", ""))
            shot.asset_seed = int(asset.get("seed", 0) or 0)
            shot.asset_first_frame_path = str(asset.get("first_frame_path", ""))
            provider_event = dict(asset.get("event") or {})
            if (provider_event or float(asset.get("cost", 0) or 0) > 0) and task.trace:
                provider_event.setdefault("type", "asset")
                provider_event.setdefault("slot", "asset")
                provider_event.setdefault("provider", asset.get("source", "unknown"))
                provider_event.setdefault("cost", float(asset.get("cost", 0) or 0))
                provider_event.setdefault("shot_id", shot.id)
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
                    media_type=str(asset.get("media_type", "")),
                    model=str(asset.get("model", "")),
                    seed=int(asset.get("seed", 0) or 0),
                    prompt=str(asset.get("prompt", "")),
                    first_frame_path=str(asset.get("first_frame_path", "")),
                    cost=float(asset.get("cost", 0) or 0),
                )
            )
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
            },
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
        total = sum(float(shot.duration_sec) for shot in task.shots)
        voice_path: Path | None = directory / "voice.wav"
        music_path: Path | None = directory / "music.wav"
        tts_provider = "none"
        tts_model = "none"

        uploaded_voice_value = str(task.audio.get("voice_local_path", "")).strip()
        uploaded_voice = Path(uploaded_voice_value) if uploaded_voice_value else None
        if task.input.voice == NO_VOICE:
            voice_path = None
        elif uploaded_voice and uploaded_voice.is_file():
            voice_path = uploaded_voice
            validate_audio_file(voice_path)
            tts_provider = "local-upload"
            tts_model = "uploaded-audio"
        else:
            preferred = self.settings.tts_provider
            if preferred == "auto":
                preferred = "openai-compatible-tts" if self.settings.tts_base_url and self.settings.tts_api_key else ""
            candidates = list(self.registry.candidates("tts", preferred))
            errors: list[str] = []
            for candidate in dict.fromkeys(candidates):
                try:
                    result = self.registry.synthesize_tts(
                        candidate,
                        task.narration,
                        voice_path,
                        task.input.voice,
                        task.input.voice_rate,
                        total,
                    )
                    validate_audio_file(Path(result["path"]))
                    tts_provider, tts_model = str(result["provider"]), str(result["model"])
                    if errors:
                        result["event"]["fallback_reason"] = "; ".join(errors)
                        if task.trace:
                            task.trace.fallback_used = True
                    if task.trace:
                        task.trace.events.append(result["event"])
                    if isinstance(result.get("cues"), list):
                        task.audio["cues"] = result["cues"]
                    break
                except Exception as exc:
                    errors.append(f"{candidate}: {exc}")
            else:
                raise RuntimeError(f"No usable TTS provider: {'; '.join(errors)}")

        if task.input.music_mood == NO_MUSIC:
            music_path = None
        else:
            uploaded_music_value = str(task.audio.get("music_local_path", "")).strip()
            uploaded_music = Path(uploaded_music_value) if uploaded_music_value else None
            if uploaded_music and uploaded_music.is_file():
                music_path = uploaded_music
                validate_audio_file(music_path)
                music_provider = "local-upload"
            else:
                candidates = [self.settings.music_provider]
                if self.settings.allow_synthetic_audio_fallback:
                    candidates.append("offline-synthetic-tone")
                errors = []
                for candidate in dict.fromkeys(candidates):
                    try:
                        music = self.registry.create("music", candidate)
                        music_path = music.resolve(directory / "music.wav", task.input.music_mood, total)
                        validate_audio_file(music_path)
                        music_provider = music.name
                        break
                    except Exception as exc:
                        errors.append(f"{candidate}: {exc}")
                else:
                    raise RuntimeError(
                        "No usable music provider. Upload music, configure MUSIC_DIR, "
                        f"or choose 无音乐. Details: {'; '.join(errors)}"
                    )

        task.audio.update(
            {
                "voice_provider": tts_provider if voice_path else "none",
                "voice_model": tts_model if voice_path else "none",
                "voice_url": self._media_url(task.id, voice_path) if voice_path else "",
                "voice_local_path": str(voice_path) if voice_path else "",
                "music_provider": locals().get("music_provider", "none") if music_path else "none",
                "music_url": self._media_url(task.id, music_path) if music_path else "",
                "music_local_path": str(music_path) if music_path else "",
                "duration_sec": total,
                "disclosure": "Audio provider and fallback state are recorded in trace.",
            }
        )
        self._sync_shots_to_audio(task)
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
                raw_cues.append({"shot_id": shot.id, "start": elapsed, "end": end, "text": shot.narration})
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
        for shot in task.shots:
            start, end = ranges[shot.id]
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
        self.update(
            task,
            stage="rendering",
            progress=84,
            event={"type": "stage", "stage": "rendering", "status": "started"},
        )
        render_shots = [shot.__dict__.copy() for shot in task.shots]
        renderer = self.registry.create("render", self.settings.render_provider)
        task.render = renderer.render(
            task.id,
            render_shots,
            task.audio,
            task.input.include_ai_label,
            task.input.include_subtitles,
            task.input.output_resolution,
            task.input.fps,
            task.input.voice_volume,
            task.input.music_volume,
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
            duration = float(patch["duration_sec"])
            if not 1 <= duration <= 20:
                raise ValueError("duration_sec must be between 1 and 20")
            proposed = sum(
                duration if item.id == shot_id else float(item.duration_sec)
                for item in task.shots
            )
            if abs(proposed - task.input.duration) > 0.01:
                raise ValueError(
                    f"Shot durations must add up to the task duration ({task.input.duration}s)"
                )
            shot.duration_sec = duration
        task.status = "draft"
        task.stage = "preview"
        task.progress = 35
        task.error = ""
        task.updated_at = now_iso()
        self.storage.save_task(task)
        return task.to_dict()
