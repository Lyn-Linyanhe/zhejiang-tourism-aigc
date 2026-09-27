"""素材 / 配音注册表。

P2、P3 按下面的入口注册，编排只查表，不再写 if-else。

画面降级链（contract.FallbackChain）：

    cloud-i2v → cloud-t2i → local-library → offline

最后一档固定是 OfflineAssetProvider。某一档失败、模块不存在或没有密钥，
就记一条 fallback_used 和原因，再试下一档。importlib 加载失败不能让服务起不来。

P2 注册入口（image.py / video.py，本文件不创建它们）：

    def build_provider(settings) -> provider
    provider.generate(shot, output_dir, style_ctx) -> AssetResult
    或 provider.create_or_find(task_id, shot, output_dir, color, accent) -> dict

P3 配音由编排调用 tts.build_audio()。云配音只在 TTS_BASE_URL 和 TTS_API_KEY 都有时启用。
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .contract import CONTRACT_VERSION, FallbackChain
from .media import OfflineAssetProvider, PexelsAssetProvider


ASSET_CHAIN = FallbackChain(
    slot="asset",
    providers=["cloud-i2v", "cloud-t2i", "local-library", "offline"],
)
ASSET_CHAIN.validate()
if ASSET_CHAIN.providers[-1] != "offline":
    raise RuntimeError("画面降级链最后一档必须是 offline（OfflineAssetProvider）")

# 配音查表顺序。是否真的走到某一档，由密钥和 TTS_PROVIDER 开关决定。
AUDIO_CHAIN = FallbackChain(
    slot="audio",
    providers=["cloud-tts", "edge-tts", "windows-sapi", "offline-tone"],
)
AUDIO_CHAIN.validate()

_OFF_SWITCHES = {"off", "disabled", "false", "0", "none", "no"}
_ASSET_ALIASES = {
    "library": "local-library",
    "ai": "cloud-i2v",
    "cloud": "cloud-i2v",
    # 旧缺省值。空密钥时仍要走完整降级链，不能收成只出技术场景图。
    "configured": "cloud-i2v",
    "auto": "cloud-i2v",
    "": "cloud-i2v",
}


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


def _instantiate(module: Any, settings: Any, kind: str) -> Any:
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


@dataclass
class ProviderStatus:
    slot: str
    name: str
    available: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass
class AcquireResult:
    value: Any
    provider: str
    fallback_used: bool
    reason: str
    attempts: list[dict[str, str]] = field(default_factory=list)


class _CloudAssetAdapter:
    """包住 image.build_provider / video.build_provider。缺模块或缺密钥时 available=False。"""

    def __init__(self, name: str, kind: str, module_name: str, settings: Any) -> None:
        self.name = name
        self.kind = kind
        self.module_name = module_name
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
        provider = _instantiate(self._module, self._settings, self.kind)
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


class _PexelsAdapter:
    name = "pexels"

    def __init__(self, settings: Any) -> None:
        self._inner = PexelsAssetProvider(settings.pexels_api_key, settings.pexels_base_url)
        self.available = bool(str(settings.pexels_api_key or "").strip())
        self.reason = "" if self.available else "未配置 PEXELS_API_KEY"

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
        style_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del style_ctx
        if not self.available:
            raise RuntimeError(self.reason)
        # User-Agent 仍由 PexelsAssetProvider 自己带，注册表不改那条请求。
        return self._inner.create_or_find(task_id, shot, output_dir, theme_color, accent)


class _OfflineAdapter:
    name = "offline"

    def __init__(self) -> None:
        self._inner = OfflineAssetProvider()
        self.available = True
        self.reason = ""

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
        style_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del style_ctx
        return self._inner.create_or_find(task_id, shot, output_dir, theme_color, accent)


class _LibraryAdapter:
    name = "local-library"

    def __init__(self) -> None:
        self.available = True
        self.reason = ""
        self._finder: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None

    def bind(self, finder: Callable[[dict[str, Any]], dict[str, Any] | None] | None) -> None:
        self._finder = finder

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
        style_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del task_id, output_dir, theme_color, accent, style_ctx
        if self._finder is None:
            raise RuntimeError("本地素材库未绑定")
        asset = self._finder(shot)
        if not asset or not Path(str(asset.get("local_path", ""))).is_file():
            raise RuntimeError("Local asset library has no usable image/video for this shot")
        return asset


class _CloudTTSAdapter:
    name = "cloud-tts"

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        switch = str(getattr(settings, "tts_provider", "") or "").strip().lower()
        # 空开关 = 沿用旧行为：有密钥就用云配音。显式 off 才关掉。
        self.available = bool(settings.tts_base_url and settings.tts_api_key) and _switch_enabled(switch)
        if not _switch_enabled(switch):
            self.reason = "TTS_PROVIDER 已关闭"
        elif not self.available:
            self.reason = "未配置 TTS_BASE_URL 或 TTS_API_KEY"
        else:
            self.reason = ""

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0, **_: Any) -> dict[str, Any]:
        from .tts import OpenAICompatibleTTSProvider

        if not self.available:
            raise RuntimeError(self.reason or "cloud-tts 不可用")
        mapped = "alloy" if voice in {"女声", "female", "alloy"} else "onyx"
        provider = OpenAICompatibleTTSProvider(
            self._settings.tts_base_url,
            self._settings.tts_api_key,
            self._settings.tts_model,
        )
        result = provider.synthesize(text, output_path, mapped)
        return {
            "path": str(result.path),
            "provider": result.provider,
            "model": result.model,
            "event": result.event,
        }


class _EdgeTTSAdapter:
    name = "edge-tts"
    available = True
    reason = ""

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0, **kwargs: Any) -> dict[str, Any]:
        from .tts import generate_edge_tts

        return generate_edge_tts(text, output_path, voice, rate, kwargs.get("shots"))


class _WindowsSAPIAdapter:
    name = "windows-sapi"

    def __init__(self) -> None:
        self.available = sys.platform.startswith("win")
        self.reason = "" if self.available else "当前不是 Windows，无法使用 SAPI"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0, **_: Any) -> dict[str, Any]:
        del voice, rate
        from .tts import generate_system_speech

        if not self.available:
            raise RuntimeError(self.reason)
        generate_system_speech(text, output_path)
        return {
            "path": str(output_path),
            "provider": "windows-system-speech",
            "model": "installed-system-voice",
        }


class _OfflineToneAdapter:
    name = "offline-tone"

    def __init__(self, settings: Any) -> None:
        self.available = bool(settings.allow_synthetic_audio_fallback)
        self.reason = "" if self.available else "ALLOW_SYNTHETIC_AUDIO_FALLBACK 未开启"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0, **kwargs: Any) -> dict[str, Any]:
        del text, voice, rate
        from .media_ffmpeg import generate_tone

        if not self.available:
            raise RuntimeError(self.reason)
        duration = float(kwargs.get("duration") or 1.0)
        frequency = int(kwargs.get("frequency") or 360)
        generate_tone(output_path, duration, frequency, 0.11)
        return {
            "path": str(output_path),
            "provider": "offline-synthetic-tone",
            "model": "explicit-fallback",
        }


class ProviderRegistry:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._library = _LibraryAdapter()
        self._assets: dict[str, Any] = {
            "cloud-i2v": _CloudAssetAdapter("cloud-i2v", "video", "video", settings),
            "cloud-t2i": _CloudAssetAdapter("cloud-t2i", "image", "image", settings),
            "local-library": self._library,
            "offline": _OfflineAdapter(),
            "pexels": _PexelsAdapter(settings),
        }
        self._audio: dict[str, Any] = {
            "cloud-tts": _CloudTTSAdapter(settings),
            "edge-tts": _EdgeTTSAdapter(),
            "windows-sapi": _WindowsSAPIAdapter(),
            "offline-tone": _OfflineToneAdapter(settings),
        }
        offline = self._assets["offline"]
        if not isinstance(offline._inner, OfflineAssetProvider):  # noqa: SLF001 - 兜底类型必须可核对
            raise RuntimeError("offline 档必须是 OfflineAssetProvider")

    def bind_library(self, finder: Callable[[dict[str, Any]], dict[str, Any] | None] | None) -> None:
        self._library.bind(finder)

    def asset_chain_for(self, source: str) -> list[str]:
        selected = str(source or "").strip().lower()
        if selected == "configured":
            selected = str(self.settings.asset_provider or "").strip().lower()
        selected = _ASSET_ALIASES.get(selected, selected)
        if selected == "upload":
            return ["upload"]
        if selected == "pexels":
            return ["pexels", "offline"]
        if selected == "offline":
            return ["offline"]
        if selected == "local-library":
            return ["local-library", "offline"]
        if selected == "cloud-t2i":
            return ["cloud-t2i", "local-library", "offline"]
        # 缺省、cloud-i2v、未知来源都走完整降级链。缺模块或缺密钥由 _acquire 跳过。
        return list(ASSET_CHAIN.providers)

    def audio_chain(self) -> list[str]:
        cloud = self._audio["cloud-tts"]
        tone = "offline-tone"
        if cloud.available:
            # 有云配音时保持旧行为：失败只降到合成音（若允许），不去改走 edge。
            return ["cloud-tts", tone] if self._audio[tone].available else ["cloud-tts"]
        chain = ["edge-tts", "windows-sapi"]
        if self._audio[tone].available:
            chain.append(tone)
        return chain

    def statuses(self) -> list[ProviderStatus]:
        rows: list[ProviderStatus] = []
        for name in ("cloud-i2v", "cloud-t2i", "local-library", "offline", "pexels"):
            provider = self._assets[name]
            rows.append(
                ProviderStatus("asset", name, bool(provider.available), str(provider.reason or ""))
            )
        for name in AUDIO_CHAIN.providers:
            provider = self._audio[name]
            rows.append(
                ProviderStatus("audio", name, bool(provider.available), str(provider.reason or ""))
            )
        return rows

    def describe(self) -> dict[str, Any]:
        from ..models import STAGES

        return {
            "contract": CONTRACT_VERSION,
            "stages": list(STAGES),
            "asset_chain": list(ASSET_CHAIN.providers),
            "audio_chain": self.audio_chain(),
            "providers": [item.to_dict() for item in self.statuses()],
        }

    def acquire_asset(
        self,
        chain: list[str],
        call: Callable[[Any], dict[str, Any]],
    ) -> AcquireResult:
        return self._acquire("asset", chain, self._assets, call)

    def acquire_audio(self, chain: list[str], call: Callable[[Any], dict[str, Any]]) -> AcquireResult:
        return self._acquire("audio", chain, self._audio, call)

    def _acquire(
        self,
        slot: str,
        chain: list[str],
        table: dict[str, Any],
        call: Callable[[Any], Any],
    ) -> AcquireResult:
        if not chain:
            raise RuntimeError(f"{slot} 降级链为空")
        attempts: list[dict[str, str]] = []
        for name in chain:
            provider = table.get(name)
            if provider is None:
                attempts.append({"provider": name, "reason": "未注册"})
                continue
            if not getattr(provider, "available", False):
                attempts.append({"provider": name, "reason": str(getattr(provider, "reason", "") or "不可用")})
                continue
            try:
                value = call(provider)
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
        raise RuntimeError(f"{slot} provider 全部失败: {detail}")
