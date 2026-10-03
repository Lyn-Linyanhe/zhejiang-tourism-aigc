from __future__ import annotations

from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any, Callable

from .contract import AssetResult, FallbackChain
from .llm import OpenAICompatibleLLMProvider
from .media import OfflineAssetProvider, PexelsAssetProvider
from .media_ffmpeg import generate_tone
from .moneyprinterturbo import MoneyPrinterTurboLLMProvider
from .music import find_music_file
from .offline import OfflineLLMProvider
from .tts import OpenAICompatibleTTSProvider, TTSResult, generate_edge_tts, generate_system_speech


Factory = Callable[[], Any]
Availability = Callable[[], bool]


@dataclass(frozen=True)
class ProviderSpec:
    slot: str
    name: str
    label: str
    factory: Factory
    description: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)
    available: Availability = lambda: True


class ProviderRegistry:
    """Provider discovery and fallback policy shared by the worker and HTTP API."""

    def __init__(self) -> None:
        self._specs: dict[str, dict[str, ProviderSpec]] = {}
        self._aliases: dict[str, dict[str, str]] = {}
        self._chains: dict[str, FallbackChain] = {}

    def register(self, spec: ProviderSpec, *, replace: bool = False) -> None:
        slot, name = spec.slot.strip().lower(), spec.name.strip().lower()
        if not slot or not name:
            raise ValueError("Provider slot and name are required")
        providers = self._specs.setdefault(slot, {})
        aliases = self._aliases.setdefault(slot, {})
        if (name in providers or name in aliases) and not replace:
            raise ValueError(f"Provider already registered: {slot}/{name}")
        if replace and name in providers:
            for alias in providers[name].aliases:
                aliases.pop(alias.strip().lower(), None)
        providers[name] = spec
        for alias in spec.aliases:
            normalized = alias.strip().lower()
            if normalized in providers or (normalized in aliases and aliases[normalized] != name):
                raise ValueError(f"Provider alias already registered: {slot}/{normalized}")
            aliases[normalized] = name

    def register_chain(self, chain: FallbackChain) -> None:
        chain.validate()
        slot = chain.slot.strip().lower()
        names = [self.resolve_name(slot, name) for name in chain.providers]
        if slot == "asset" and names[-1] != "offline":
            raise ValueError("Asset fallback chain must end with the offline provider")
        self._chains[slot] = FallbackChain(slot=slot, providers=names)

    def resolve_name(self, slot: str, name: str) -> str:
        normalized_slot, normalized_name = slot.strip().lower(), name.strip().lower()
        canonical = self._aliases.get(normalized_slot, {}).get(normalized_name, normalized_name)
        if canonical not in self._specs.get(normalized_slot, {}):
            choices = ", ".join(self.names(normalized_slot)) or "none"
            raise ValueError(f"Unknown {normalized_slot} provider {name!r}; registered: {choices}")
        return canonical

    def create(self, slot: str, name: str) -> Any:
        normalized_slot = slot.strip().lower()
        canonical = self.resolve_name(normalized_slot, name)
        spec = self._specs[normalized_slot][canonical]
        if not spec.available():
            raise RuntimeError(f"Provider is not configured or available: {normalized_slot}/{canonical}")
        provider = spec.factory()
        if provider is None:
            raise RuntimeError(f"Provider factory returned nothing: {normalized_slot}/{canonical}")
        return provider

    def generate_asset(
        self,
        name: str,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        style_ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke either the frozen AssetResult protocol or a legacy asset provider."""
        provider = self.create("asset", name)
        if hasattr(provider, "generate"):
            result = provider.generate(shot, output_dir, style_ctx)
            required = ("path", "media_type", "source", "model", "seed", "prompt", "cost", "event")
            if not isinstance(result, AssetResult) and not all(hasattr(result, field_name) for field_name in required):
                raise TypeError(f"{name} must return an AssetResult-compatible object")
            result.validate()
            path = Path(result.path)
            payload = {
                "id": f"{name}_{shot.get('id', path.stem)}",
                "name": f"{name}-{shot.get('title', path.stem)}",
                "url": f"/media/{task_id}/assets/{path.name}",
                "local_path": str(path),
                "source": result.source,
                "license": "Provider-generated asset; publication rights require review",
                "author": result.model or name,
                "usage_scope": "technical-validation-pending-rights-review",
                "is_ai_generated": True,
                "media_type": result.media_type,
                "model": result.model,
                "seed": result.seed,
                "prompt": result.prompt,
                "first_frame_path": result.first_frame_path,
                "cost": result.cost,
                "event": result.event,
            }
            return payload
        if hasattr(provider, "create_or_find"):
            payload = provider.create_or_find(
                task_id,
                shot,
                output_dir,
                str(style_ctx.get("color", "#2d6c63")),
                str(style_ctx.get("accent", "#d6ad60")),
            )
            suffix = Path(str(payload.get("local_path", ""))).suffix.lower()
            payload.setdefault("media_type", "video" if suffix in {".mp4", ".mov", ".webm", ".mkv"} else "image")
            payload.setdefault("model", str(getattr(provider, "name", name)))
            payload.setdefault("seed", 0)
            payload.setdefault("prompt", str(shot.get("visual_prompt", "")))
            payload.setdefault("first_frame_path", "")
            payload.setdefault("cost", 0.0)
            payload.setdefault("event", {})
            return payload
        raise TypeError(f"Asset provider {name!r} implements neither generate nor create_or_find")

    def synthesize_tts(
        self,
        name: str,
        text: str,
        output_path: Path,
        voice: str,
        rate: float,
        duration: float,
    ) -> dict[str, Any]:
        provider = self.create("tts", name)
        parameters = signature(provider.synthesize).parameters
        result = (
            provider.synthesize(text, output_path, voice, rate, duration)
            if len(parameters) >= 5
            else provider.synthesize(text, output_path, voice, rate)
        )
        if isinstance(result, TTSResult):
            return {
                "path": result.path, "provider": result.provider, "model": result.model,
                "event": result.event,
            }
        if not isinstance(result, dict) or not result.get("path"):
            raise TypeError(f"TTS provider {name!r} must return TTSResult or a result mapping")
        result.setdefault("provider", getattr(provider, "name", name))
        result.setdefault("model", "unknown")
        result.setdefault("event", {"type": "tts", "provider": result["provider"], "ok": True})
        return result

    def candidates(self, slot: str, preferred: str = "") -> tuple[str, ...]:
        normalized_slot = slot.strip().lower()
        names: list[str] = []
        if preferred:
            names.append(self.resolve_name(normalized_slot, preferred))
        chain = self._chains.get(normalized_slot)
        if chain:
            names.extend(chain.providers)
        return tuple(dict.fromkeys(names))

    def names(self, slot: str) -> tuple[str, ...]:
        return tuple(self._specs.get(slot.strip().lower(), {}))

    def describe(self) -> dict[str, list[dict[str, Any]]]:
        return {
            slot: [{
                "name": spec.name, "label": spec.label, "description": spec.description,
                "aliases": list(spec.aliases), "available": bool(spec.available()),
            } for spec in providers.values()]
            for slot, providers in self._specs.items()
        }

    def describe_chains(self) -> dict[str, list[str]]:
        return {slot: list(chain.providers) for slot, chain in self._chains.items()}


class _RequiredUploadAssetProvider:
    name = "upload"

    def create_or_find(self, task_id: str, shot: dict[str, Any], output_dir: Path, theme_color: str, accent: str) -> dict[str, Any]:
        raise RuntimeError(
            f"Shot {shot.get('order', '?')} has no uploaded image/video asset. "
            "Upload an asset for every shot or select another asset provider."
        )


class _LocalLibraryAssetProvider:
    name = "local-library"

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def create_or_find(self, task_id: str, shot: dict[str, Any], output_dir: Path, theme_color: str, accent: str) -> dict[str, Any]:
        query = str(shot.get("library_query") or shot.get("visual_prompt") or shot.get("title") or "")
        asset = self.storage.find_library_asset("image", query) or self.storage.find_library_asset("video", query)
        asset = asset or self.storage.find_library_asset("image") or self.storage.find_library_asset("video")
        if not asset or not Path(str(asset.get("local_path", ""))).is_file():
            raise RuntimeError("Local asset library has no usable image/video for this shot")
        return {
            "id": asset["id"], "name": asset.get("name", shot.get("title", "library asset")),
            "url": asset.get("url", ""), "local_path": asset["local_path"], "source": self.name,
            "license": asset.get("license_status", "pending-review"), "author": asset.get("author", "unknown"),
            "usage_scope": asset.get("usage_scope", "technical-validation"),
            "is_ai_generated": bool(asset.get("is_ai_generated", False)),
        }


class _EdgeTTSAdapter:
    name = "edge-tts"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float, duration: float) -> TTSResult:
        raw = generate_edge_tts(text, output_path, voice, rate)
        model = str(raw.get("model", voice))
        return TTSResult(output_path, self.name, model, {"type": "tts", "provider": self.name, "model": model, "ok": True})


class _WindowsTTSAdapter:
    name = "windows-system-speech"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float, duration: float) -> TTSResult:
        generate_system_speech(text, output_path)
        return TTSResult(output_path, self.name, "installed-system-voice", {"type": "tts", "provider": self.name, "model": "installed-system-voice", "ok": True})


class _OpenAITTSAdapter:
    name = "openai-compatible-tts"

    def __init__(self, provider: OpenAICompatibleTTSProvider) -> None:
        self.provider = provider

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float, duration: float) -> TTSResult:
        mapped_voice = {"女声": "alloy", "男声": "onyx"}.get(voice, voice)
        return self.provider.synthesize(text, output_path, mapped_voice)


class _SyntheticTTSAdapter:
    name = "offline-synthetic-tone"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float, duration: float) -> TTSResult:
        generate_tone(output_path, duration, 520 if voice == "女声" else 360, 0.11)
        return TTSResult(output_path, self.name, "explicit-fallback", {"type": "tts", "provider": self.name, "model": "explicit-fallback", "ok": True})


class _LocalMusicAdapter:
    name = "local-music-library"

    def __init__(self, music_dir: Path) -> None:
        self.music_dir = music_dir

    def resolve(self, output_path: Path, mood: str, duration: float) -> Path:
        path = find_music_file(self.music_dir)
        if not path:
            raise RuntimeError("No supported audio file exists in MUSIC_DIR")
        return path


class _SyntheticMusicAdapter:
    name = "offline-synthetic-tone"
    frequencies = {"舒缓": 220, "热烈": 330, "雅致": 260, "轻快": 440}

    def resolve(self, output_path: Path, mood: str, duration: float) -> Path:
        generate_tone(output_path, duration, self.frequencies.get(mood, 220), 0.08)
        return output_path


class _PendingProvider:
    def __init__(self, name: str) -> None:
        raise RuntimeError(f"Provider {name} is configured but its implementation is not installed")


def build_provider_registry(settings: Any, storage: Any) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ProviderSpec("llm", "offline", "浙江文旅离线编辑器", OfflineLLMProvider, "无需密钥的本地脚本兜底"))
    registry.register(ProviderSpec("llm", "openai-compatible", "OpenAI 兼容文本模型", lambda: OpenAICompatibleLLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model), "DeepSeek 等兼容 chat/completions 的服务", ("deepseek", "openai"), lambda: bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)))
    registry.register(ProviderSpec("llm", "moneyprinterturbo", "MoneyPrinterTurbo", lambda: MoneyPrinterTurboLLMProvider(settings.mpt_root, settings.mpt_timeout_seconds, settings.mpt_python), "隔离子进程桥接", ("mpt",), lambda: settings.mpt_root.exists()))

    registry.register(ProviderSpec("asset", "ai-image", "AI 生成图片", lambda: _PendingProvider("ai-image"), "等待 P2 注册具体实现", available=lambda: False))
    registry.register(ProviderSpec("asset", "ai-video", "AI 生成视频", lambda: _PendingProvider("ai-video"), "等待 P2 注册具体实现", available=lambda: False))
    registry.register(ProviderSpec("asset", "pexels", "Pexels 在线素材", lambda: PexelsAssetProvider(settings.pexels_api_key, settings.pexels_base_url), "需 PEXELS_API_KEY", available=lambda: bool(settings.pexels_api_key)))
    registry.register(ProviderSpec("asset", "local-library", "本地素材库", lambda: _LocalLibraryAssetProvider(storage), "从已登记素材中检索", ("library",)))
    registry.register(ProviderSpec("asset", "upload", "逐镜头上传", _RequiredUploadAssetProvider, "要求每个镜头已有上传素材"))
    registry.register(ProviderSpec("asset", "offline", "技术测试场景图", OfflineAssetProvider, "本地生成、需人工审核", ("offline-scene-generator",)))

    registry.register(ProviderSpec("tts", "openai-compatible-tts", "OpenAI 兼容 TTS", lambda: _OpenAITTSAdapter(OpenAICompatibleTTSProvider(settings.tts_base_url, settings.tts_api_key, settings.tts_model)), "外部语音合成服务", ("openai",), lambda: bool(settings.tts_base_url and settings.tts_api_key)))
    registry.register(ProviderSpec("tts", "edge-tts", "Edge TTS", _EdgeTTSAdapter, "本机 Python edge-tts 模块"))
    registry.register(ProviderSpec("tts", "windows-system-speech", "Windows 系统语音", _WindowsTTSAdapter, "使用系统已安装语音"))
    registry.register(ProviderSpec("tts", "offline-synthetic-tone", "合成测试音", _SyntheticTTSAdapter, "仅用于显式开启的技术测试", available=lambda: bool(settings.allow_synthetic_audio_fallback)))

    registry.register(ProviderSpec("music", "local-music-library", "本地音乐库", lambda: _LocalMusicAdapter(settings.music_dir), "读取 MUSIC_DIR 中的授权音乐", available=lambda: find_music_file(settings.music_dir) is not None))
    registry.register(ProviderSpec("music", "offline-synthetic-tone", "合成测试音", _SyntheticMusicAdapter, "仅用于显式开启的技术测试", available=lambda: bool(settings.allow_synthetic_audio_fallback)))

    from ..services.renderer import VideoRenderer
    registry.register(ProviderSpec("render", "ffmpeg", "FFmpeg", lambda: VideoRenderer(settings.media_dir), "本地 MP4、字幕和音频合成"))

    asset_chain = [name for name in settings.asset_fallback_providers if name in registry.names("asset")]
    if not asset_chain or asset_chain[-1] != "offline":
        asset_chain.append("offline")
    registry.register_chain(FallbackChain(slot="asset", providers=asset_chain))
    tts_chain = [name for name in settings.tts_fallback_providers if name in registry.names("tts")]
    if tts_chain:
        registry.register_chain(FallbackChain(slot="tts", providers=tts_chain))
    return registry
