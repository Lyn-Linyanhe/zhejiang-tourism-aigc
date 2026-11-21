"""Editable integration settings for the local workbench."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from ..config import INHERITED_ENV, Settings


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    choices: tuple[str, ...] = ()


GROUPS = (
    ("文本模型（OpenAI 兼容）", (
        Field("LLM_BASE_URL", "API 基础地址", "url"),
        Field("LLM_MODEL", "模型 ID"),
        Field("LLM_API_KEY", "API KEY", "password"),
    )),
    ("AI 画面", (
        Field("IMAGE_BASE_URL", "API 基础地址", "url"),
        Field("IMAGE_MODEL", "模型 ID"),
        Field("IMAGE_API_STYLE", "接口协议", "select", ("auto", "openai-compatible", "tokendance")),
        Field("IMAGE_API_KEY", "API KEY", "password"),
    )),
    ("AI 视频", (
        Field("VIDEO_BASE_URL", "API 基础地址", "url"),
        Field("VIDEO_MODEL", "模型 ID"),
        Field("VIDEO_API_STYLE", "接口协议", "select", ("auto", "volcengine", "tokendance")),
        Field("VIDEO_API_KEY", "API KEY", "password"),
    )),
    ("配音声库", (
        Field("TTS_BASE_URL", "API 基础地址", "url"),
        Field("TTS_MODEL", "模型 ID"),
        Field("TTS_API_KEY", "API KEY", "password"),
        Field("TTS_VOICE_CATALOG", "音色 ID 与显示名（JSON）", "catalog"),
    )),
    ("Pexels", (
        Field("PEXELS_BASE_URL", "API 基础地址", "url"),
        Field("PEXELS_API_KEY", "API KEY", "password"),
    )),
    ("Pixabay", (Field("PIXABAY_API_KEY", "API KEY", "password"),)),
)
FIELDS = {field.name: field for _, fields in GROUPS for field in fields}
KEYS_BY_URL = {
    "LLM_BASE_URL": "LLM_API_KEY",
    "IMAGE_BASE_URL": "IMAGE_API_KEY",
    "VIDEO_BASE_URL": "VIDEO_API_KEY",
    "TTS_BASE_URL": "TTS_API_KEY",
    "PEXELS_BASE_URL": "PEXELS_API_KEY",
}


def _api_url(value: str):
    parts = urlsplit(value)
    port = parts.port
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or "?" in value
        or "#" in value
        or chr(92) in value
        or (port is not None and port == 0)
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError("请输入不含登录信息、查询参数或片段的 HTTP(S) 基础地址")
    return parts


def _origin(value: str) -> tuple:
    if not value:
        return ()
    url = _api_url(value)
    return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80)


def uses_local_speech(settings: Settings) -> bool:
    if not settings.local_tts_enabled or not settings.tts_base_url:
        return False
    try:
        address = _api_url(settings.tts_base_url)
    except ValueError:
        return False
    return (
        address.hostname in {"127.0.0.1", "::1", "localhost"}
        and (address.port or 80) == settings.port
        and address.path.rstrip("/") == "/v1"
    )


def public_settings(settings: Settings, *, include_keys: bool = False) -> dict:
    groups = []
    for title, fields in GROUPS:
        controls = []
        for field in fields:
            value = getattr(settings, field.name.lower())
            control = {"name": field.name, "label": field.label, "kind": field.kind,
                       "locked": field.name in INHERITED_ENV}
            if field.kind == "password":
                control["configured"] = bool(value)
                if include_keys:
                    control["value"] = value
            else:
                if field.kind == "url" and value:
                    try:
                        _api_url(value)
                    except ValueError:
                        value = ""
                control["value"] = value
                if field.choices:
                    control["choices"] = field.choices
            controls.append(control)
        groups.append({"title": title, "fields": controls})
    return {"groups": groups, "local_speech": uses_local_speech(settings)}


def _voice_catalog(value: str) -> str:
    try:
        catalog = json.loads(value or "{}")
    except ValueError:
        raise ValueError("音色目录必须是 JSON 对象") from None
    if not isinstance(catalog, dict) or any(
        not 0 < len(key) <= 80 or not isinstance(label, str) or not label.strip()
        for key, label in catalog.items()
    ):
        raise ValueError("音色目录须由音色 ID 和显示名组成")
    return json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))


def validate_changes(settings: Settings, submitted: dict) -> dict[str, str]:
    updates = {}
    for name, raw in submitted.items():
        field = FIELDS.get(name)
        if not field or name in INHERITED_ENV or not isinstance(raw, str):
            raise ValueError("包含不可编辑的配置项")
        value = raw.strip()
        if not value and field.kind == "password":
            continue
        if field.kind == "catalog":
            value = _voice_catalog(value)
        if len(value) > (12_000 if field.kind == "catalog" else 4096) or len(value.splitlines()) > 1 or any(
            ord(char) < 32 or ord(char) == 127 for char in value
        ):
            raise ValueError(f"{name} 长度超限或包含控制字符")
        if field.kind == "url" and value:
            try:
                _api_url(value)
            except ValueError as exc:
                raise ValueError(f"{name} 需要填写有效的 HTTP(S) API 基础地址，地址中请勿包含 KEY") from exc
            value = value.rstrip("/")
        if field.choices and value not in field.choices:
            raise ValueError(f"{name} 接口协议不受支持")
        if field.kind == "password" and not value.isascii():
            raise ValueError(f"{name} 请使用服务商提供的原始 KEY")
        if value != getattr(settings, name.lower()):
            updates[name] = value

    for url_name, key_name in KEYS_BY_URL.items():
        if not updates.get(url_name) or not getattr(settings, key_name.lower()):
            continue
        try:
            old_origin = _origin(getattr(settings, url_name.lower()))
        except ValueError:
            old_origin = ()
        if old_origin != _origin(updates[url_name]) and not submitted.get(key_name, "").strip():
            raise ValueError(f"{url_name} 更换了服务地址，请同时输入该地址对应的 KEY")
    return updates


def write_dotenv(path: Path, updates: dict[str, str]) -> None:
    content = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = []
    remaining = dict(updates)
    for line in content.splitlines():
        name = line.split("=", 1)[0].strip()
        if "=" not in line or name not in updates:
            lines.append(line)
        elif name in remaining:
            lines.append(f'{name}="{remaining.pop(name)}"')
    lines.extend(f'{name}="{value}"' for name, value in remaining.items())
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                 prefix=".provider-settings-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write("\n".join(lines) + "\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
