from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        value = value.strip().strip("\"'")
        if name and name not in os.environ:
            os.environ[name] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value.strip() if value is not None else default


@dataclass(frozen=True)
class Settings:
    host: str = _env("APP_HOST", "127.0.0.1")
    port: int = int(_env("APP_PORT", "8787"))
    data_dir: Path = PROJECT_ROOT / _env("DATA_DIR", "runtime")
    default_provider: str = _env("DEFAULT_PROVIDER", "offline")
    allow_provider_fallback: bool = _env("ALLOW_PROVIDER_FALLBACK", "false").lower() in {"1", "true", "yes"}
    mpt_root: Path = Path(
        _env("MPT_ROOT", str(PROJECT_ROOT.parent / "MoneyPrinterTurbo"))
    )
    mpt_python: str = _env("MPT_PYTHON")
    mpt_timeout_seconds: int = int(_env("MPT_TIMEOUT_SECONDS", "180"))
    llm_base_url: str = _env("LLM_BASE_URL")
    llm_api_key: str = _env("LLM_API_KEY")
    llm_model: str = _env("LLM_MODEL", "gpt-4o-mini")
    llm_timeout_seconds: int = int(_env("LLM_TIMEOUT_SECONDS", "180"))
    tts_base_url: str = _env("TTS_BASE_URL")
    tts_api_key: str = _env("TTS_API_KEY")
    tts_model: str = _env("TTS_MODEL", "default")
    # 空字符串表示未显式关闭，有密钥才启用云配音。
    tts_provider: str = _env("TTS_PROVIDER")
    image_base_url: str = _env("IMAGE_BASE_URL")
    image_api_key: str = _env("IMAGE_API_KEY")
    image_model: str = _env("IMAGE_MODEL")
    image_provider: str = _env("IMAGE_PROVIDER", "auto")
    video_base_url: str = _env("VIDEO_BASE_URL")
    video_api_key: str = _env("VIDEO_API_KEY")
    video_model: str = _env("VIDEO_MODEL")
    video_provider: str = _env("VIDEO_PROVIDER", "auto")
    music_base_url: str = _env("MUSIC_BASE_URL")
    music_api_key: str = _env("MUSIC_API_KEY")
    music_model: str = _env("MUSIC_MODEL")
    music_provider: str = _env("MUSIC_PROVIDER", "library")
    # 缺省走完整降级链。显式 offline 才只出技术测试场景图。
    asset_provider: str = _env("ASSET_PROVIDER", "cloud-i2v")
    pexels_api_key: str = _env("PEXELS_API_KEY")
    pexels_base_url: str = _env("PEXELS_BASE_URL", "https://api.pexels.com/v1")
    music_dir: Path = PROJECT_ROOT / _env("MUSIC_DIR", "runtime/music")
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


settings = Settings()
