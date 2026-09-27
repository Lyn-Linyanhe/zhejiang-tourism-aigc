from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


# v1：配音在画面之前。review 仍是完成后的展示态，不进重试链。
STAGES = ("scripting", "storyboard", "audio", "assets", "rendering", "review")
PIPELINE_STAGES = ("scripting", "storyboard", "audio", "assets", "rendering")
ALLOWED_ASSET_SOURCES = {
    "configured",
    "offline",
    "upload",
    "pexels",
    "local-library",
    "library",
    "cloud-i2v",
    "cloud-t2i",
    "ai",
    "cloud",
}
TERMINAL_STATUSES = ("completed", "failed")
ALLOWED_DURATIONS = (15, 30, 60)
ALLOWED_SHOT_COUNTS = (0, 4, 6, 8)
ALLOWED_RESOLUTIONS = {
    "720x1280": (720, 1280),
    "1080x1920": (1080, 1920),
    "1920x1080": (1920, 1080),
    "1080x1080": (1080, 1080),
}
ALLOWED_STYLES = ("诗意纪实", "城市漫游", "国风雅韵", "青春活力", "节庆热烈")
ALLOWED_VOICES = ("女声", "男声", "无配音")
ALLOWED_MUSIC_MOODS = ("舒缓", "热烈", "雅致", "轻快", "无音乐")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class TaskInput:
    city: str
    landmark: str
    theme: str
    culture: str
    festival: str
    audience: str
    duration: int
    style: str
    voice: str
    music_mood: str
    include_ai_label: bool = True
    custom_brief: str = ""
    shot_count: int = 0
    include_subtitles: bool = True
    asset_source: str = "configured"
    output_resolution: str = "720x1280"
    fps: int = 25
    voice_rate: float = 1.0
    voice_volume: float = 1.0
    music_volume: float = 0.18

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TaskInput":
        values = {
            "city": str(raw.get("city", "")).strip(),
            "landmark": str(raw.get("landmark", "")).strip(),
            "theme": str(raw.get("theme", "")).strip(),
            "culture": str(raw.get("culture", "")).strip(),
            "festival": str(raw.get("festival", "")).strip(),
            "audience": str(raw.get("audience", "")).strip(),
            "duration": int(raw.get("duration", 30)),
            "style": str(raw.get("style", "诗意纪实")).strip(),
            "voice": str(raw.get("voice", "女声")).strip(),
            "music_mood": str(raw.get("music_mood", "舒缓")).strip(),
            "include_ai_label": bool(raw.get("include_ai_label", True)),
            "custom_brief": str(raw.get("custom_brief", "")).strip(),
            "shot_count": int(raw.get("shot_count", 0)),
            "include_subtitles": bool(raw.get("include_subtitles", True)),
            "asset_source": str(raw.get("asset_source", "configured")).strip().lower(),
            "output_resolution": str(raw.get("output_resolution", "720x1280")).strip().lower(),
            "fps": int(raw.get("fps", 25)),
            "voice_rate": float(raw.get("voice_rate", 1.0)),
            "voice_volume": float(raw.get("voice_volume", 1.0)),
            "music_volume": float(raw.get("music_volume", 0.18)),
        }
        if not values["city"]:
            raise ValueError("city 不能为空")
        if not values["theme"]:
            raise ValueError("theme 不能为空")
        if len(values["theme"]) > 80:
            raise ValueError("theme 长度不能超过 80 个字符")
        if len(values["custom_brief"]) > 500:
            raise ValueError("custom_brief 长度不能超过 500 个字符")
        if values["shot_count"] not in ALLOWED_SHOT_COUNTS:
            raise ValueError("shot_count must be 4, 6, or 8")
        if values["duration"] not in ALLOWED_DURATIONS:
            raise ValueError(f"duration 只能是 {ALLOWED_DURATIONS}")
        if values["asset_source"] not in ALLOWED_ASSET_SOURCES:
            raise ValueError(
                "asset_source must be configured, offline, upload, pexels, "
                "local-library, cloud-i2v, cloud-t2i, or ai"
            )
        if values["output_resolution"] not in ALLOWED_RESOLUTIONS:
            raise ValueError(f"output_resolution must be one of {tuple(ALLOWED_RESOLUTIONS)}")
        if values["fps"] not in (24, 25, 30, 60):
            raise ValueError("fps must be 24, 25, 30, or 60")
        if not 0.5 <= values["voice_rate"] <= 2.0:
            raise ValueError("voice_rate must be between 0.5 and 2.0")
        if not 0.0 <= values["voice_volume"] <= 2.0:
            raise ValueError("voice_volume must be between 0 and 2")
        if not 0.0 <= values["music_volume"] <= 1.0:
            raise ValueError("music_volume must be between 0 and 1")
        for field_name in ("style", "voice", "music_mood"):
            if not values[field_name] or len(values[field_name]) > 80:
                raise ValueError(f"{field_name} must be non-empty and no longer than 80 characters")
        return cls(**values)


@dataclass
class Shot:
    id: str
    order: int
    duration_sec: float
    title: str
    visual_prompt: str
    narration: str
    subtitle: str
    transition: str = "fade"
    asset_url: str = ""
    asset_id: str = ""
    asset_local_path: str = ""
    asset_media_type: str = ""
    audio_url: str = ""


@dataclass
class AssetRecord:
    id: str
    task_id: str
    shot_id: str
    name: str
    url: str
    source: str
    license: str
    author: str
    usage_scope: str
    is_ai_generated: bool
    local_path: str = ""
    created_at: str = field(default_factory=now_iso)


@dataclass
class TraceRecord:
    provider: str
    model: str
    prompt_version: str
    fallback_used: bool
    created_at: str = field(default_factory=now_iso)
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Task:
    id: str
    input: TaskInput
    status: str = "queued"
    stage: str = "queued"
    progress: int = 0
    title: str = ""
    summary: str = ""
    cta: str = ""
    tags: list[str] = field(default_factory=list)
    narration: str = ""
    shots: list[Shot] = field(default_factory=list)
    assets: list[AssetRecord] = field(default_factory=list)
    audio: dict[str, Any] = field(default_factory=dict)
    render: dict[str, Any] = field(default_factory=dict)
    trace: TraceRecord | None = None
    error: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    return cleaned.strip("-")[:50] or "tourism-video"
