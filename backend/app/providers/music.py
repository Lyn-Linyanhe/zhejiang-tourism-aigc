"""Licensed task-upload and local-catalog music matching."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from .contract import MusicProviderProtocol, MusicResult


SUPPORTED_MUSIC_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
)


def authorized(record: dict[str, Any]) -> bool:
    if record.get("approved") is not True:
        return False
    fields = [str(record.get(key) or "").strip() for key in ("license", "author", "usage_scope")]
    uncertain = ("未知", "待确认", "自行确认", "待填写", "未确认", "未授权", "pending", "unknown", "unverified")
    if record.get("authorization_basis") == "uploader_assertion":
        return all(fields) and not any(term in fields[0].casefold() for term in uncertain)
    return all(fields) and not any(term in field.casefold() for field in fields for term in uncertain)


def _result_from_record(record: dict[str, Any], path: Path, provider: str) -> MusicResult:
    # 手工维护的 catalog 条目常常只写 file 不写 id，缺了就退回文件名，
    # 不然会在 MusicResult.validate() 上炸掉，看起来像"曲目没被选中"。
    result = MusicResult(
        id=str(record.get("id") or path.stem),
        path=str(path),
        moods=[str(value) for value in record.get("moods", [])],
        styles=[str(value) for value in record.get("styles", [])],
        license=str(record.get("license", "")),
        author=str(record.get("author", "")),
        usage_scope=str(record.get("usage_scope", "")),
        approved=bool(record.get("approved", False)),
        provider=provider,
        model="metadata-exact-match-v1",
        event={"type": "music-match", "provider": provider},
        title=str(record.get("title") or path.stem),
        authorization_basis=str(record.get("authorization_basis") or "catalog_metadata"),
        authorization_ref=str(record.get("authorization_ref") or record.get("source_url") or record.get("license_url") or ""),
    )
    result.validate()
    return result


def read_music_catalog(catalog_path: Path) -> tuple[dict | list, list]:
    payload = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else {"tracks": []}
    records = payload.get("tracks") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("Music catalog must contain a tracks array")
    return payload, records


def load_music_catalog(catalog_path: Path, music_dir: Path) -> list[dict[str, Any]]:
    _, records = read_music_catalog(catalog_path)
    usable: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict) or not authorized(raw):
            continue
        file_value = str(raw.get("file", "")).strip()
        path = Path(file_value)
        if not path.is_absolute():
            path = music_dir / path
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_MUSIC_SUFFIXES:
            continue
        record = dict(raw)
        record["resolved_path"] = str(path.resolve())
        usable.append(record)
    return usable


def select_music(
    *,
    task_audio: dict[str, Any],
    mood: str,
    style: str,
    duration: float,
    catalog_path: Path,
    music_dir: Path,
    cloud_enabled: bool = False,
    cloud_provider: MusicProviderProtocol | None = None,
    output_dir: Path | None = None,
) -> MusicResult:
    """Select one approved track for the whole video in deterministic order."""
    uploaded = task_audio.get("music_upload")
    if isinstance(uploaded, dict):
        upload_path = Path(str(uploaded.get("path", "")))
        upload_moods = [str(value) for value in uploaded.get("moods", [])]
        upload_styles = [str(value) for value in uploaded.get("styles", [])]
        if (
            upload_path.is_file()
            and authorized(uploaded)
            and mood in upload_moods
            and style in upload_styles
        ):
            return _result_from_record(uploaded, upload_path.resolve(), "task-upload")

    # 同情绪多曲时随机选一首：曲库有几十首，固定取第一首会让每支片子配同一段音乐。
    # 选中的曲目 id 会进 MusicResult，manifest 的 music_match 能追到实际用了哪首。
    matches = [
        record
        for record in load_music_catalog(catalog_path, music_dir)
        if mood in record.get("moods", []) and style in record.get("styles", [])
    ]
    if matches:
        record = random.choice(matches)
        result = _result_from_record(record, Path(record["resolved_path"]), "local-catalog")
        result.event = {
            "type": "music-match",
            "provider": "local-catalog",
            "candidates": len(matches),
            "chosen": result.id,
        }
        return result

    if cloud_enabled:
        if cloud_provider is None:
            raise RuntimeError(
                "Cloud music is enabled but no MusicProviderProtocol implementation was injected"
            )
        result = cloud_provider.select(mood, style, duration, output_dir or music_dir)
        result.validate()
        if not authorized(result.to_dict()):
            raise RuntimeError("音乐许可待确认")
        return result

    raise RuntimeError(
        f"No approved music matches mood={mood!r} and style={style!r}. "
        "Upload an authorized track or configure an injected cloud music provider."
    )


class CloudMusicProviderStub:
    """Injection point only; it never calls a paid provider by itself."""

    name = "cloud-music-stub"

    def select(self, mood: str, style: str, duration: float, output_dir: Any) -> MusicResult:
        raise RuntimeError(
            "Cloud music provider is a stub. Inject a licensed MusicProviderProtocol implementation."
        )
