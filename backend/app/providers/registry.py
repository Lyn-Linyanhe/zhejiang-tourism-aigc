from __future__ import annotations

import importlib
import sys
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


@dataclass
class AcquireResult:
    """一次降级链尝试的结果：命中的 provider、是否降级、失败原因与逐档记录。"""

    value: Any
    provider: str
    fallback_used: bool
    reason: str
    attempts: list[dict[str, str]] = field(default_factory=list)


# --- P2 画面 provider 的适配层（来自 A 的 registry，用于填充 P1 预留的 asset 槽位）---

_OFF_SWITCHES = {"off", "disabled", "false", "0", "none", "no"}


def _switch_enabled(value: str) -> bool:
    return str(value or "").strip().lower() not in _OFF_SWITCHES


def _import_optional(short_name: str) -> Any | None:
    """尝试加载 providers.image / providers.video。不存在或导入失败都返回 None。"""
    package = __package__ or "app.providers"
    candidates = (
        f"{package}.{short_name}",
        f"app.providers.{short_name}",
        f"backend.app.providers.{short_name}",
    )
    seen: set[str] = set()
    for mod_name in candidates:
        if mod_name in seen:
            continue
        seen.add(mod_name)
        try:
            return importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            target_missing = (
                missing == mod_name
                or missing.endswith("." + short_name)
                or missing == short_name
            )
            if target_missing:
                continue
            # 模块在，但内部依赖缺失。跳过，不能因此让服务起不来。
            return None
        except Exception:
            return None
    return None


def _has_factory(module: Any) -> bool:
    if module is None:
        return False
    if callable(getattr(module, "build_provider", None)) or callable(
        getattr(module, "create_provider", None)
    ):
        return True
    for class_name in (
        "CloudVideoProvider",
        "VideoProvider",
        "CloudImageProvider",
        "ImageProvider",
        "Provider",
    ):
        if isinstance(getattr(module, class_name, None), type):
            return True
    return False


def _instantiate(module: Any, settings: Any, kind: str, factory_name: str = "build_provider") -> Any:
    # factory_name 允许同一个模块暴露多个入口（video.py 的 build_provider 走图生视频、
    # build_t2v_provider 走纯文生视频），缺了再退回默认名。
    factory = getattr(module, factory_name, None)
    if not callable(factory):
        factory = getattr(module, "build_provider", None) or getattr(module, "create_provider", None)
    if callable(factory):
        try:
            return factory(settings)
        except TypeError:
            return factory(
                base_url=getattr(settings, f"{kind}_base_url", ""),
                api_key=getattr(settings, f"{kind}_api_key", ""),
                model=getattr(settings, f"{kind}_model", ""),
            )
    for class_name in (
        "CloudVideoProvider",
        "VideoProvider",
        "CloudImageProvider",
        "ImageProvider",
        "Provider",
    ):
        cls = getattr(module, class_name, None)
        if not isinstance(cls, type):
            continue
        try:
            return cls(settings)
        except TypeError:
            return cls(
                getattr(settings, f"{kind}_base_url", ""),
                getattr(settings, f"{kind}_api_key", ""),
                getattr(settings, f"{kind}_model", ""),
            )
    raise RuntimeError(f"providers.{kind} 没有 build_provider/create_provider")


def invoke_create_or_find(
    provider: Any,
    task_id: str,
    shot: dict[str, Any],
    output_dir: Path,
    style_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调 create_or_find，把 style_ctx 传给声明了它的 provider。

    老 provider（upload / local-library）只收 5 个参数，传第 6 个会 TypeError。
    所以按签名判断，别一律多传——它们是冻结合同的一部分。
    """
    ctx = dict(style_ctx or {})
    arguments = [
        task_id,
        shot,
        output_dir,
        str(ctx.get("color", "#2d6c63")),
        str(ctx.get("accent", "#d6ad60")),
    ]
    if "style_ctx" in signature(provider.create_or_find).parameters:
        arguments.append(ctx)
    return provider.create_or_find(*arguments)


def _asset_dict_from_any(result: Any, task_id: str, shot: dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, dict):
        if not result.get("local_path"):
            raise RuntimeError("素材结果缺少 local_path")
        return result
    path = Path(str(getattr(result, "path", "") or ""))
    if not path:
        raise RuntimeError("素材结果缺少 path")
    source = str(getattr(result, "source", "") or "cloud")
    model = str(getattr(result, "model", "") or "")
    payload = {
        "id": f"{source}_{shot.get('id', path.stem)}",
        "name": str(shot.get("title") or path.name),
        "url": f"/media/{task_id}/assets/{path.name}",
        "local_path": str(path),
        "source": source,
        "license": "AI 生成画面，公开发布前需人工审核",
        "author": model or source,
        "usage_scope": "演示、内部评审、待授权替换",
        "is_ai_generated": True,
        "media_type": str(getattr(result, "media_type", "") or ""),
        "model": model,
        "seed": int(getattr(result, "seed", 0) or 0),
        "cost": float(getattr(result, "cost", 0) or 0),
        "first_frame_path": str(getattr(result, "first_frame_path", "") or ""),
        "prompt": str(getattr(result, "prompt", "") or ""),
    }
    validate = getattr(result, "validate", None)
    if callable(validate):
        validate()
    return payload


class _CloudAssetAdapter:
    """包住 image.build_provider / video.build_provider。缺模块或缺密钥时 available=False。"""

    def __init__(
        self, name: str, kind: str, module_name: str, settings: Any, factory_name: str = "build_provider"
    ) -> None:
        self.name = name
        self.kind = kind
        self.module_name = module_name
        self.factory_name = factory_name
        self._settings = settings
        self._module = _import_optional(module_name)
        self.available, self.reason = self._availability()

    def _availability(self) -> tuple[bool, str]:
        switch = getattr(self._settings, f"{self.kind}_provider", "")
        if not _switch_enabled(switch):
            return False, f"{self.kind.upper()}_PROVIDER 已关闭"
        if self._module is None:
            return False, f"providers.{self.module_name} 不存在或导入失败"
        if not _has_factory(self._module):
            return False, f"providers.{self.module_name} 没有 build_provider"
        resolve = getattr(self._module, "resolve_config", None)
        config = resolve(self._settings) if callable(resolve) else {}
        base_url = str(config.get("base_url") or getattr(self._settings, f"{self.kind}_base_url", "") or "").strip()
        api_key = str(config.get("api_key") or getattr(self._settings, f"{self.kind}_api_key", "") or "").strip()
        model = str(config.get("model") or getattr(self._settings, f"{self.kind}_model", "") or "").strip()
        # 文生图允许空密钥（本地 ComfyUI 网关）。图生视频没有密钥就不要去提交。
        if self.kind == "video" and not api_key:
            return False, "未配置 VIDEO_API_KEY"
        if not base_url or not model:
            return False, f"未配置 {self.kind.upper()}_BASE_URL 或 {self.kind.upper()}_MODEL"
        return True, ""

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
        style_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available or self._module is None:
            raise RuntimeError(self.reason or f"{self.name} 不可用")
        provider = _instantiate(self._module, self._settings, self.kind, self.factory_name)
        ctx = dict(style_ctx or {})
        ctx.setdefault("color", theme_color)
        ctx.setdefault("accent", accent)
        ctx.setdefault("duration_sec", shot.get("duration_sec"))
        ctx.setdefault("task_id", task_id)
        if hasattr(provider, "generate"):
            result = provider.generate(shot, output_dir, ctx)
        elif hasattr(provider, "create_or_find"):
            result = provider.create_or_find(task_id, shot, output_dir, theme_color, accent)
        else:
            raise RuntimeError(f"{self.name} 没有 generate/create_or_find")
        return _asset_dict_from_any(result, task_id, shot)


class ProviderRegistry:
    """Provider discovery and fallback policy shared by the worker and HTTP API."""

    def __init__(self) -> None:
        self._specs: dict[str, dict[str, ProviderSpec]] = {}
        self._aliases: dict[str, dict[str, str]] = {}
        self._chains: dict[str, FallbackChain] = {}
        # ASSET_PROVIDER 的缺省值，供 asset_source="configured" 解析
        self.asset_source_default: str = ""

    # 用户可选的素材来源 → 降级链。名字用本注册表的规范名。
    _SOURCE_CHAINS: dict[str, tuple[str, ...]] = {
        "upload": ("upload",),
        "pexels": ("pexels", "offline"),
        "offline": ("offline",),
        "library": ("local-library", "offline"),
        "local-library": ("local-library", "offline"),
        "cloud-t2i": ("ai-image", "local-library", "offline"),
        "ai-image": ("ai-image", "local-library", "offline"),
        "ai": ("ai-video", "ai-image", "local-library", "offline"),
        "cloud": ("ai-video", "ai-image", "local-library", "offline"),
        "cloud-i2v": ("ai-video", "ai-image", "local-library", "offline"),
        "ai-video": ("ai-video", "ai-image", "local-library", "offline"),
        # 文生视频：纯文本直接出片，不经过文生图
        "ai-t2v": ("ai-t2v",),
        "cloud-t2v": ("ai-t2v",),
    }

    def asset_chain_for(self, source: str) -> list[str]:
        """把素材来源映射成一条降级链。缺省/未知来源走注册的完整 asset 链。"""
        selected = str(source or "").strip().lower()
        if selected in {"", "configured"}:
            selected = str(self.asset_source_default or "").strip().lower()
        mapped = self._SOURCE_CHAINS.get(selected)
        if mapped:
            return list(mapped)
        chain = self._chains.get("asset")
        return list(chain.providers) if chain else ["offline"]

    def acquire(self, slot: str, chain: list[str], call: Callable[[str, Any], Any]) -> AcquireResult:
        """逐档尝试降级链。call 接收 (规范名, provider)，返回该档的产物。

        某一档不可用或抛错就记一条 attempts，再试下一档；全失败才抛。
        """
        slot_key = slot.strip().lower()
        if not chain:
            raise RuntimeError(f"{slot_key} 降级链为空")
        attempts: list[dict[str, str]] = []
        for name in chain:
            try:
                provider = self.create(slot_key, name)
            except Exception as exc:
                attempts.append({"provider": name, "reason": str(exc) or exc.__class__.__name__})
                continue
            try:
                value = call(name, provider)
            except Exception as exc:
                attempts.append({"provider": name, "reason": str(exc) or exc.__class__.__name__})
                continue
            fallback_used = name != chain[0] or bool(attempts)
            reason = "; ".join(f"{item['provider']}: {item['reason']}" for item in attempts)
            return AcquireResult(
                value=value,
                provider=name,
                fallback_used=fallback_used,
                reason=reason,
                attempts=attempts,
            )
        detail = "; ".join(f"{item['provider']}: {item['reason']}" for item in attempts) or "无可用 provider"
        raise RuntimeError(f"{slot_key} provider 全部失败: {detail}")

    def acquire_asset(self, chain: list[str], call: Callable[[str, Any], Any]) -> AcquireResult:
        return self.acquire("asset", chain, call)

    def acquire_audio(self, chain: list[str], call: Callable[[str, Any], Any]) -> AcquireResult:
        return self.acquire("tts", chain, call)

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
            payload = invoke_create_or_find(provider, task_id, shot, output_dir, style_ctx)
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

    def has(self, slot: str, name: str) -> bool:
        """该 slot 下是否注册过这个名字（含别名）。"""
        normalized_slot, normalized_name = slot.strip().lower(), name.strip().lower()
        if normalized_name in self._specs.get(normalized_slot, {}):
            return True
        return bool(self._aliases.get(normalized_slot, {}).get(normalized_name))

    def candidates(self, slot: str, preferred: str = "") -> tuple[str, ...]:
        normalized_slot = slot.strip().lower()
        names: list[str] = []
        # preferred 允许是开关值而不是 provider 名（例如 TTS_PROVIDER=auto）。
        # 不是注册名就忽略它，直接走注册的降级链，不要在这里抛 Unknown provider。
        if preferred and self.has(normalized_slot, preferred):
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


def build_provider_registry(settings: Any, storage: Any) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.asset_source_default = str(getattr(settings, "asset_provider", "") or "")
    registry.register(ProviderSpec("llm", "offline", "浙江文旅离线编辑器", OfflineLLMProvider, "无需密钥的本地脚本兜底"))
    registry.register(ProviderSpec("llm", "openai-compatible", "OpenAI 兼容文本模型", lambda: OpenAICompatibleLLMProvider(settings.llm_base_url, settings.llm_api_key, settings.llm_model), "DeepSeek 等兼容 chat/completions 的服务", ("deepseek", "openai"), lambda: bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model)))
    registry.register(ProviderSpec("llm", "moneyprinterturbo", "MoneyPrinterTurbo", lambda: MoneyPrinterTurboLLMProvider(settings.mpt_root, settings.mpt_timeout_seconds, settings.mpt_python), "隔离子进程桥接", ("mpt",), lambda: settings.mpt_root.exists()))

    # 画面 provider：文生图 / 图生视频 / 文生视频。缺密钥或模块时 available=False，
    # 降级链自动跳过。
    #
    # 标签必须能分辨出画面动不起来：ai-image 只出静帧，运动全靠渲染器推近；
    # 另两个才是真的视频模型。之前 ai-image 和 ai-video 都叫"AI 生成画面"，
    # 用户选到静帧那条，得到的正是"只是把图放大、移动"的观感。
    _ai_image = _CloudAssetAdapter("cloud-t2i", "image", "image", settings)
    _ai_video = _CloudAssetAdapter("cloud-i2v", "video", "video", settings)
    _ai_t2v = _CloudAssetAdapter(
        "cloud-t2v", "video", "video", settings, factory_name="build_t2v_provider"
    )
    registry.register(ProviderSpec(
        "asset", "ai-image", "AI 静帧（画面不动）", lambda: _ai_image,
        "只生成一张图，运动来自渲染器的推近。最便宜，但画面本身不会动。",
        ("cloud-t2i",), lambda: _ai_image.available,
    ))
    registry.register(ProviderSpec(
        "asset", "ai-video", "AI 图生视频（有运动）", lambda: _ai_video,
        "先出首帧图，再让它动起来。单镜约 80~110 秒、2 次付费调用。",
        ("cloud-i2v", "ai", "cloud"), lambda: _ai_video.available,
    ))
    registry.register(ProviderSpec(
        "asset", "ai-t2v", "AI 文生视频（有运动，最快）", lambda: _ai_t2v,
        "纯文本直接出视频，不走文生图。单镜约 30 秒、1 次付费调用，画面与机位由模型自己组织。",
        ("cloud-t2v",), lambda: _ai_t2v.available,
    ))
    registry.register(ProviderSpec("asset", "pexels", "Pexels 在线素材", lambda: PexelsAssetProvider(settings.pexels_api_key, settings.pexels_base_url), "需 PEXELS_API_KEY", available=lambda: bool(settings.pexels_api_key)))
    registry.register(ProviderSpec("asset", "local-library", "本地素材库", lambda: _LocalLibraryAssetProvider(storage), "从已登记素材中检索", ("library",)))
    registry.register(ProviderSpec("asset", "upload", "逐镜头上传", _RequiredUploadAssetProvider, "要求每个镜头已有上传素材"))
    registry.register(ProviderSpec("asset", "offline", "技术测试场景图", OfflineAssetProvider, "本地生成、需人工审核", ("offline-scene-generator",)))

    registry.register(ProviderSpec("tts", "openai-compatible-tts", "OpenAI 兼容 TTS", lambda: _OpenAITTSAdapter(OpenAICompatibleTTSProvider(settings.tts_base_url, settings.tts_api_key, settings.tts_model)), "外部语音合成服务", ("openai",), lambda: bool(settings.tts_base_url and settings.tts_api_key)))
    registry.register(ProviderSpec(
        "tts", "edge-tts", "Edge TTS", _EdgeTTSAdapter,
        "需 .venv-audio 隔离运行时（scripts/setup-audio-runtime.ps1）",
        available=lambda: settings.audio_python.exists(),
    ))
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
