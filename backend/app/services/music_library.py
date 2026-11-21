"""Import a reusable background track into the local music catalog."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ..models import ALLOWED_MUSIC_MOODS, ALLOWED_STYLES
from ..providers.media_ffmpeg import validate_audio_file
from ..providers.music import SUPPORTED_MUSIC_SUFFIXES, load_music_catalog, read_music_catalog


from .uploads import upload_music_rights


MAX_TRACK_BYTES = 25 * 1024 * 1024
_catalog_lock = threading.Lock()


def _public_track(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id") or Path(record["file"]).stem,
        "title": record.get("title") or Path(record["file"]).stem,
        "moods": record.get("moods", []),
        "styles": record.get("styles", []),
        "author": record["author"],
        "license": record["license"],
        "usage_scope": record["usage_scope"],
        "authorization_basis": record.get("authorization_basis", ""),
        "authorization_ref": record.get("authorization_ref", ""),
    }


def list_music_library(catalog_path: Path, music_dir: Path) -> dict[str, Any]:
    return {"items": [_public_track(record) for record in load_music_catalog(catalog_path, music_dir)],
            "max_bytes": MAX_TRACK_BYTES}


def import_music(data: dict[str, Any], catalog_path: Path, music_dir: Path) -> dict[str, Any]:
    name = str(data.get("name", ""))
    suffix = Path(name).suffix.lower()
    title = Path(name).stem.strip()
    if suffix not in SUPPORTED_MUSIC_SUFFIXES or not 0 < len(title) <= 200:
        raise ValueError("请选择有效的 MP3、WAV、M4A、FLAC、AAC、OGG、OPUS 或 WMA 文件。")
    encoded = data.get("data_base64", "")
    if not isinstance(encoded, str) or len(encoded) > (MAX_TRACK_BYTES + 2) // 3 * 4 + 128:
        raise ValueError("曲库单个音频文件最大为 25 MB。")
    mood, style = data.get("mood"), data.get("style", "")
    if mood not in ALLOWED_MUSIC_MOODS[:-1] or (style and style not in ALLOWED_STYLES):
        raise ValueError("请选择曲目的情绪和适用风格。")
    record = upload_music_rights(data)
    try:
        audio = base64.b64decode(encoded.rsplit(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("音频上传数据无效。") from exc
    if not audio or len(audio) > MAX_TRACK_BYTES:
        raise ValueError("请选择 25 MB 以内的有效音频文件。")

    # Retrying the same file updates its tags instead of adding duplicate copies.
    track_id = f"local-{hashlib.sha256(suffix.encode() + audio).hexdigest()[:24]}"
    relative = Path("imported") / f"{track_id}{suffix}"
    target = music_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    record.update({"id": track_id, "title": title, "file": relative.as_posix(),
                   "moods": [mood], "styles": [style] if style else list(ALLOWED_STYLES)})
    pending_audio = None
    pending_catalog = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".music-", suffix=suffix, delete=False) as file:
            pending_audio = Path(file.name)
            file.write(audio)
        try:
            validate_audio_file(pending_audio)
        except RuntimeError as exc:
            raise ValueError("无法解码所选文件的音频，请检查文件格式。") from exc
        with _catalog_lock:
            catalog, entries = read_music_catalog(catalog_path)
            updated = [entry for entry in entries if not isinstance(entry, dict) or entry.get("id") != track_id]
            updated.append(record)
            if isinstance(catalog, dict):
                catalog["tracks"] = updated
            else:
                catalog = updated
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=catalog_path.parent,
                                     prefix=".music-catalog-", suffix=".tmp", delete=False) as file:
                pending_catalog = Path(file.name)
                json.dump(catalog, file, ensure_ascii=False, indent=2)
                file.write("\n")
            new_file = not target.exists()
            if new_file:
                pending_audio.replace(target)
            try:
                pending_catalog.replace(catalog_path)
            except OSError:
                if new_file:
                    target.unlink(missing_ok=True)
                raise
    finally:
        for pending in (pending_audio, pending_catalog):
            if pending is not None:
                pending.unlink(missing_ok=True)
    return _public_track(record)
