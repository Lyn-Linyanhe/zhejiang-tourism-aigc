from __future__ import annotations

import re
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


STAGES = ("scripting", "storyboard", "audio", "assets", "rendering", "review")
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
ALLOWED_MUSIC_DUCKING = ("不压低", "轻微", "标准")


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
    subject_lock: str = ""
    subject_reference_path: str = ""
    shot_count: int = 0
    include_subtitles: bool = True
    asset_source: str = "configured"
    output_resolution: str = "1080x1920"
    fps: int = 30
    voice_rate: float = 1.0
    # edge 的音高偏移（赫兹）。0 = 不变调。
    voice_pitch: float = 0.0
    voice_style: str = "natural"
    voice_expressiveness: float = 0.5
    voice_engine: str = "configured"
    voice_volume: float = 1.0
    music_volume: float = 0.18
    music_ducking: str = "轻微"

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
            "subject_lock": str(raw.get("subject_lock", "")).strip(),
            "subject_reference_path": str(raw.get("subject_reference_path", "")).strip(),
            "shot_count": int(raw.get("shot_count", 0)),
            "include_subtitles": bool(raw.get("include_subtitles", True)),
            "asset_source": str(raw.get("asset_source", "configured")).strip().lower(),
            "output_resolution": str(raw.get("output_resolution", cls.output_resolution)).strip().lower(),
            "fps": int(raw.get("fps", cls.fps)),
            "voice_rate": float(raw.get("voice_rate", 1.0)),
            "voice_pitch": float(raw.get("voice_pitch", 0.0)),
            "voice_style": str(raw.get("voice_style", "natural")),
            "voice_expressiveness": float(raw.get("voice_expressiveness", 0.5)),
            "voice_engine": str(raw.get("voice_engine", "configured")),
            "voice_volume": float(raw.get("voice_volume", 1.0)),
            "music_volume": float(raw.get("music_volume", 0.18)),
            "music_ducking": str(raw.get("music_ducking", cls.music_ducking)).strip(),
        }
        # Retired source: preserve its upload-only behavior for historical drafts.
        if values["voice_engine"] == "xstudio":
            values["voice_engine"] = "upload"
            values["voice"] = "上传配音音频"
        if values["voice_engine"] == "tianyi":
            values.update(voice_engine="reselect", voice="请选择新音色")
        if not values["city"]:
            raise ValueError("city 不能为空")
        if not values["theme"]:
            raise ValueError("theme 不能为空")
        if len(values["theme"]) > 80:
            raise ValueError("theme 长度不能超过 80 个字符")
        if len(values["custom_brief"]) > 500:
            raise ValueError("custom_brief 长度不能超过 500 个字符")
        if len(values["subject_lock"]) > 500:
            raise ValueError("subject_lock 长度不能超过 500 个字符")
        if len(values["subject_reference_path"]) > 1000:
            raise ValueError("subject_reference_path 长度不能超过 1000 个字符")
        if values["shot_count"] not in ALLOWED_SHOT_COUNTS:
            raise ValueError("shot_count must be 4, 6, or 8")
        if values["duration"] not in ALLOWED_DURATIONS:
            raise ValueError(f"duration 只能是 {ALLOWED_DURATIONS}")
        if values["asset_source"] not in {
            "configured", "offline", "upload", "pexels", "pixabay", "stock",
            "local-library", "library",
            "ai-image", "ai-video", "ai-t2v",
            # P2 注册表使用的旧名称，作为别名一并接受
            "cloud-i2v", "cloud-t2i", "cloud-t2v", "ai", "cloud",
        }:
            raise ValueError("asset_source must be a registered asset provider")
        if values["output_resolution"] not in ALLOWED_RESOLUTIONS:
            raise ValueError(f"output_resolution must be one of {tuple(ALLOWED_RESOLUTIONS)}")
        if values["fps"] not in (24, 25, 30, 60):
            raise ValueError("fps must be 24, 25, 30, or 60")
        if not 0.5 <= values["voice_rate"] <= 2.0:
            raise ValueError("voice_rate must be between 0.5 and 2.0")
        if not -50.0 <= values["voice_pitch"] <= 50.0:
            raise ValueError("voice_pitch must be between -50 and 50 Hz")
        if values["voice_style"] not in {"natural", "warm", "story", "bright", "neutral"}:
            raise ValueError("未知的配音语气")
        if not math.isfinite(values["voice_expressiveness"]) or not 0 <= values["voice_expressiveness"] <= 1:
            raise ValueError("语气强度必须在 0 到 1 之间")
        if values["voice_engine"] not in {"configured", "edge", "windows", "upload", "voicebank", "reselect"}:
            raise ValueError("未知的配音来源")
        if not 0.0 <= values["voice_volume"] <= 2.0:
            raise ValueError("voice_volume must be between 0 and 2")
        if not 0.0 <= values["music_volume"] <= 1.0:
            raise ValueError("music_volume must be between 0 and 1")
        if values["music_ducking"] not in ALLOWED_MUSIC_DUCKING:
            raise ValueError(f"music_ducking 只能是 {ALLOWED_MUSIC_DUCKING}")
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
    motion: str = "auto"
    focus_x: float | None = None
    focus_y: float | None = None
    asset_url: str = ""
    asset_id: str = ""
    asset_local_path: str = ""
    asset_media_type: str = ""
    asset_model: str = ""
    subject_group: str = ""
    subject_reference: str = ""
    asset_seed: int = 0
    asset_first_frame_path: str = ""
    audio_url: str = ""
    audio_local_path: str = ""
    voice_style: str = "inherit"
    voice_rate_scale: float = 1.0
    voice_pitch_offset: float = 0.0
    voice_pause_after: float | None = None


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
    grantor: str = ""
    authorization_ref: str = ""
    selection_key: str = ""
    external_id: str = ""
    page_url: str = ""
    location_evidence: str = ""
    search_query: str = ""
    local_path: str = ""
    media_type: str = ""
    model: str = ""
    seed: int = 0
    prompt: str = ""
    first_frame_path: str = ""
    cost: float = 0.0
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
    visual_qa: dict[str, Any] = field(default_factory=dict)
    trace: TraceRecord | None = None
    error: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    initial_input: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    return cleaned.strip("-")[:50] or "tourism-video"
