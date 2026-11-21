from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INHERITED_ENV = frozenset(os.environ)


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, value = text.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value is not None else default


def _env_list(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in _env(name, default).split(",") if item.strip())


def _project_path(name: str, default: str) -> Path:
    value = Path(_env(name, default)).expanduser()
    return value if value.is_absolute() else PROJECT_ROOT / value


@dataclass(frozen=True)
class Settings:
    host: str = _env("APP_HOST", "127.0.0.1")
    port: int = int(_env("APP_PORT", "8787"))
    data_dir: Path = PROJECT_ROOT / _env("DATA_DIR", "runtime")
    default_provider: str = _env("DEFAULT_PROVIDER", "offline")
    llm_fallback_providers: tuple[str, ...] = _env_list("LLM_FALLBACK_PROVIDERS", "offline")
    allow_provider_fallback: bool = _env("ALLOW_PROVIDER_FALLBACK", "false").lower() in {"1", "true", "yes"}
    mpt_root: Path = Path(
        _env("MPT_ROOT", str(PROJECT_ROOT.parent / "MoneyPrinterTurbo"))
    )
    mpt_python: str = _env("MPT_PYTHON")
    mpt_timeout_seconds: int = int(_env("MPT_TIMEOUT_SECONDS", "180"))
    llm_base_url: str = _env("LLM_BASE_URL")
    llm_api_key: str = _env("LLM_API_KEY")
    llm_model: str = _env("LLM_MODEL", "gpt-4o-mini")
    local_tts_enabled: bool = _env("LOCAL_TTS_ENABLED", "false").lower() in {"1", "true", "yes"}
    tts_base_url: str = _env("TTS_BASE_URL")
    tts_api_key: str = _env("TTS_API_KEY")
    tts_model: str = _env("TTS_MODEL", "default")
    tts_provider: str = _env("TTS_PROVIDER", "auto")
    tts_fallback_providers: tuple[str, ...] = _env_list(
        "TTS_FALLBACK_PROVIDERS", "edge-tts,windows-system-speech,offline-synthetic-tone"
    )
    # P3 逐镜配音运行时（可选重依赖，缺省 .venv-audio 不存在时回落系统语音）
    audio_python: Path = _project_path(
        "AUDIO_PYTHON", r".venv-audio\Scripts\python.exe"
    )
    audio_device: str = _env("AUDIO_DEVICE", "cuda").lower()
    audio_compute_type: str = _env("AUDIO_COMPUTE_TYPE", "float16").lower()
    subtitle_alignment: str = _env("SUBTITLE_ALIGNMENT", "auto").lower()
    whisper_model: str = _env("WHISPER_MODEL", "small")
    audio_model_cache: Path = _project_path("AUDIO_MODEL_CACHE", r"runtime\models")
    audio_allow_cpu_fallback: bool = _env(
        "AUDIO_ALLOW_CPU_FALLBACK", "true"
    ).lower() in {"1", "true", "yes"}
    audio_timeout_seconds: int = int(_env("AUDIO_TIMEOUT_SECONDS", "900"))
    # 逐镜素材生成的并发数。单镜实测约 110 秒（文生图 37~70s + 图生视频 44s），
    # 串行跑 4 镜要 8 分钟。网关限流时把这个值调小。
    asset_concurrency: int = max(1, int(_env("ASSET_CONCURRENCY", "4") or 4))
    asset_provider: str = _env("ASSET_PROVIDER", "offline")
    asset_fallback_providers: tuple[str, ...] = _env_list(
        "ASSET_FALLBACK_PROVIDERS", "local-library,offline"
    )
    image_provider: str = _env("IMAGE_PROVIDER", "")
    image_api_style: str = _env("IMAGE_API_STYLE", "auto")
    image_base_url: str = _env("IMAGE_BASE_URL", "https://tokendance.space/gateway/ark/v3")
    image_api_key: str = _env("IMAGE_API_KEY")
    image_model: str = _env("IMAGE_MODEL", "seedream-5.0-pro")
    image_size: str = _env("IMAGE_SIZE", "2K")
    image_seed: int = int(_env("IMAGE_SEED", "0"))
    image_style_prefix: str = _env("IMAGE_STYLE_PREFIX")
    image_negative_prompt: str = _env("IMAGE_NEGATIVE_PROMPT")
    video_provider: str = _env("VIDEO_PROVIDER", "")
    video_api_style: str = _env("VIDEO_API_STYLE", "auto")
    video_base_url: str = _env("VIDEO_BASE_URL", "https://tokendance.space/gateway/minimax/v2")
    video_api_key: str = _env("VIDEO_API_KEY")
    video_model: str = _env("VIDEO_MODEL", "minimax-h3")
    video_resolution: str = _env("VIDEO_RESOLUTION", "768P")
    video_ratio: str = _env("VIDEO_RATIO", "9:16")
    video_poll_interval: int = int(_env("VIDEO_POLL_INTERVAL", "5"))
    video_timeout: int = int(_env("VIDEO_TIMEOUT", "1800"))
    pixabay_api_key: str = _env("PIXABAY_API_KEY")
    tts_voice_catalog: str = _env("TTS_VOICE_CATALOG", "{}")
    pexels_api_key: str = _env("PEXELS_API_KEY")
    pexels_base_url: str = _env("PEXELS_BASE_URL", "https://api.pexels.com/v1")
    music_dir: Path = PROJECT_ROOT / _env("MUSIC_DIR", "runtime/music")
    music_catalog_path: Path = _project_path(
        "MUSIC_CATALOG_PATH", r"runtime\music\catalog.json"
    )
    cloud_music_enabled: bool = _env("CLOUD_MUSIC_ENABLED", "false").lower() in {
        "1", "true", "yes"
    }
    music_provider: str = _env("MUSIC_PROVIDER", "local-music-library")
    music_base_url: str = _env("MUSIC_BASE_URL")
    music_api_key: str = _env("MUSIC_API_KEY")
    music_model: str = _env("MUSIC_MODEL")
    render_provider: str = _env("RENDER_PROVIDER", "ffmpeg")
    allow_synthetic_audio_fallback: bool = _env(
        "ALLOW_SYNTHETIC_AUDIO_FALLBACK", "false"
    ).lower() in {"1", "true", "yes"}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "app.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.audio_model_cache.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "diagnostics").mkdir(parents=True, exist_ok=True)


settings = Settings()
