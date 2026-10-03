from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from typing import Any

from ..config import Settings
from ..models import AssetRecord, Shot, Task, TaskInput, TraceRecord, now_iso, new_id
from ..providers.llm import LLMProvider, OpenAICompatibleLLMProvider
from ..providers.media import OfflineAssetProvider, PexelsAssetProvider
from ..providers.media_ffmpeg import generate_tone, validate_audio_file
from ..providers.moneyprinterturbo import MoneyPrinterTurboLLMProvider
from ..providers.music import find_music_file
from ..providers.offline import OfflineLLMProvider
from ..providers.tts import (
    OpenAICompatibleTTSProvider,
    generate_edge_tts,
    generate_system_speech,
)
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
        if from_stage not in ("scripting", "storyboard", "assets", "audio", "rendering"):
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
            self.update(task, status="running", stage=from_stage or "scripting", progress=5)
            if from_stage in (None, "scripting"):
                self._scripting(task)
                from_stage = "storyboard"
            if from_stage in (None, "storyboard", "scripting"):
                self._storyboard(task)
                from_stage = "assets"
            if from_stage in (None, "assets", "storyboard", "scripting"):
                self._assets(task)
                from_stage = "audio"
            if from_stage in (None, "audio", "assets", "storyboard", "scripting"):
                self._audio(task)
                from_stage = "rendering"
            if from_stage in (None, "rendering", "audio", "assets", "storyboard", "scripting"):
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
        prompt = json.dumps(task.input.__dict__, ensure_ascii=False, sort_keys=True)
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
            progress=48,
            event={"type": "stage", "stage": "assets", "status": "started"},
        )
        directory = self.settings.media_dir / task.id / "assets"
        knowledge = task.audio.get("knowledge", {})
        asset_source = (
            self.settings.asset_provider
            if task.input.asset_source == "configured"
            else task.input.asset_source
        )
        if asset_source == "pexels":
            provider = PexelsAssetProvider(
                self.settings.pexels_api_key,
                self.settings.pexels_base_url,
            )
        else:
            provider = OfflineAssetProvider()
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
            elif asset_source == "upload":
                raise RuntimeError(
                    f"Shot {shot.order} has no uploaded image/video asset. "
                    "Upload an asset for every shot or select Pexels/offline assets."
                )
            elif asset_source in {"local-library", "library"}:
                query = f"{task.input.city} {task.input.theme} {shot.title}"
                library_asset = self.storage.find_library_asset("image", query)
                if not library_asset:
                    library_asset = self.storage.find_library_asset("video", query)
                if not library_asset:
                    library_asset = self.storage.find_library_asset("image") or self.storage.find_library_asset("video")
                if not library_asset or not Path(str(library_asset.get("local_path", ""))).is_file():
                    raise RuntimeError("Local asset library has no usable image/video for this shot")
                asset = {
                    "id": library_asset["id"], "name": library_asset.get("name", shot.title),
                    "url": library_asset.get("url", ""), "local_path": library_asset["local_path"],
                    "source": "local-library", "license": library_asset.get("license_status", "pending-review"),
                    "author": library_asset.get("author", "unknown"), "usage_scope": library_asset.get("usage_scope", "technical-validation"),
                    "is_ai_generated": bool(library_asset.get("is_ai_generated", False)),
                }
            else:
                asset = provider.create_or_find(
                    task.id,
                    shot.__dict__,
                    directory,
                    knowledge.get("color", "#2d6c63"),
                    knowledge.get("accent", "#d6ad60"),
                )
            shot.asset_url = asset["url"]
            shot.asset_id = asset["id"]
            shot.asset_local_path = asset["local_path"]
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
            progress=58,
            event={
                "type": "stage",
                "stage": "assets",
                "status": "completed",
                "asset_count": len(task.assets),
                "source": asset_source,
            },
        )

    def _audio(self, task: Task) -> None:
        self.update(
            task,
            stage="audio",
            progress=67,
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
        elif self.settings.tts_base_url and self.settings.tts_api_key:
            try:
                tts = OpenAICompatibleTTSProvider(
                    self.settings.tts_base_url,
                    self.settings.tts_api_key,
                    self.settings.tts_model,
                )
                result = tts.synthesize(
                    task.narration,
                    voice_path,
                    "alloy" if task.input.voice == FEMALE_VOICE else "onyx",
                )
                validate_audio_file(result.path)
                tts_provider = result.provider
                tts_model = result.model
                if task.trace:
                    task.trace.events.append(result.event)
            except Exception as exc:
                if not self.settings.allow_synthetic_audio_fallback:
                    raise RuntimeError(f"TTS provider failed: {exc}") from exc
                generate_tone(voice_path, total, 520 if task.input.voice == FEMALE_VOICE else 360, 0.11)
                tts_provider = "offline-synthetic-tone"
                tts_model = "explicit-fallback"
                if task.trace:
                    task.trace.events.append({"type": "tts", "provider": tts_provider, "ok": False, "message": str(exc)})
        else:
            try:
                try:
                    edge = generate_edge_tts(
                        task.narration,
                        voice_path,
                        task.input.voice,
                        task.input.voice_rate,
                    )
                    tts_provider = edge["provider"]
                    tts_model = edge["model"]
                except Exception:
                    generate_system_speech(task.narration, voice_path)
                    validate_audio_file(voice_path)
                    tts_provider = "windows-system-speech"
                    tts_model = "installed-system-voice"
                if task.trace:
                    task.trace.events.append(
                        {
                            "type": "tts",
                            "provider": tts_provider,
                            "model": tts_model,
                            "ok": True,
                        }
                    )
            except Exception as exc:
                if not self.settings.allow_synthetic_audio_fallback:
                    raise RuntimeError(f"No usable TTS provider: {exc}") from exc
                generate_tone(voice_path, total, 520 if task.input.voice == FEMALE_VOICE else 360, 0.11)
                tts_provider = "offline-synthetic-tone"
                tts_model = "explicit-fallback"
                if task.trace:
                    task.trace.events.append({"type": "tts", "provider": tts_provider, "ok": False, "message": str(exc)})

        if task.input.music_mood == NO_MUSIC:
            music_path = None
        else:
            uploaded_music_value = str(task.audio.get("music_local_path", "")).strip()
            uploaded_music = Path(uploaded_music_value) if uploaded_music_value else None
            configured_music = find_music_file(self.settings.music_dir)
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
                "music_url": self._media_url(task.id, music_path) if music_path else "",
                "music_local_path": str(music_path) if music_path else "",
                "duration_sec": total,
                "disclosure": "Audio provider and fallback state are recorded in trace.",
            }
        )
        self.update(
            task,
            stage="audio",
            progress=76,
            event={"type": "stage", "stage": "audio", "status": "completed", "duration_sec": total},
        )

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
