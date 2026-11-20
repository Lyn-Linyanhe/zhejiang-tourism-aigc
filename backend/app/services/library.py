"""File intake helpers for the reusable entity asset library."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import AssetRecord
from ..storage import Storage, library_provenance, validate_library_metadata
from . import knowledge


MAX_LIBRARY_UPLOAD_BYTES = 20 * 1024 * 1024
_EXTENSIONS = {
    ".jpg": "image", ".jpeg": "image", ".png": "image",
    ".webp": "image", ".bmp": "image",
    ".mp4": "video", ".mov": "video", ".m4v": "video",
    ".webm": "video",
}


def normalize_upload_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "entity_id", "city", "district", "place_name", "shot_tags", "source_url",
        "source_title", "source_publisher", "author", "license", "license_url",
        "license_evidence", "usage_scope", "valid_until", "retrieved_at",
        "is_ai_generated",
    }
    normalized = validate_library_metadata({key: value for key, value in metadata.items() if key in fields})
    city = knowledge.canonical_city(normalized.get("city") or "")
    entity_id = str(normalized.get("entity_id") or "").strip()
    if entity_id:
        entity = knowledge.get_entity(entity_id, city)
        if not entity:
            raise ValueError("entity_id is not in the selected city knowledge base")
        city = entity["city"]
        normalized.setdefault("place_name", entity["name"])
    normalized.update(city=city, entity_id=entity_id)
    return normalized


def library_asset_record(task_id: str, shot_id: str, asset: dict[str, Any]) -> AssetRecord:
    return AssetRecord(
        id=f"{task_id}_{shot_id}_{asset['id']}", task_id=task_id, shot_id=shot_id,
        name=asset["name"], url=asset.get("url", ""), source="local-library",
        license=asset.get("license") or "Pending team verification",
        author=asset.get("author") or "Pending team verification",
        usage_scope=asset.get("usage_scope") or "Pending team verification",
        is_ai_generated=bool(asset.get("is_ai_generated", False)),
        local_path=asset["local_path"], media_type=asset["asset_type"],
        provenance=library_provenance(asset),
    )


def save_library_upload(
    storage: Storage,
    library_dir: Path,
    name: str,
    content: bytes,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    metadata = normalize_upload_metadata(metadata)
    suffix = Path(name).suffix.lower()
    asset_type = _EXTENSIONS.get(suffix)
    if asset_type is None:
        raise ValueError("Only image and video uploads are supported")
    if not content or len(content) > MAX_LIBRARY_UPLOAD_BYTES:
        raise ValueError("A non-empty media file up to 20MB is required")
    digest = hashlib.sha256(content).hexdigest()
    asset_id = f"library_{digest[:16]}"
    existing = storage.get_library_asset(asset_id)
    if existing:
        if (existing.get("entity_id") or "") != metadata["entity_id"]:
            raise ValueError("This file is already registered to a different entity")
        if not set(metadata.get("shot_tags", [])).issubset(existing.get("shot_tags", [])):
            raise ValueError("This file is already registered with different shot tags; edit its metadata separately")
        if not storage.library_file_matches(existing, Path(existing["local_path"])):
            raise ValueError("The registered library file has changed; review it before uploading again")
        return existing
    target_dir = library_dir / digest[:2]
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{digest}{suffix}"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("The library destination contains a different file")
    else:
        path.write_bytes(content)
    payload = {
        **metadata,
        "id": asset_id,
        "name": name[:255],
        "local_path": str(path.resolve()),
        "url": f"/library-media/{path.relative_to(library_dir).as_posix()}",
        "asset_type": asset_type,
        "source": "local-upload",
        "classification": "ai-generated" if metadata.get("is_ai_generated") else "external-candidate",
        "review_status": "pending",
        "license_status": "pending",
        "allowed_usages": ["preview"],
        "shot_tags": metadata.get("shot_tags", []),
        "retrieved_at": metadata.get("retrieved_at") or datetime.now(timezone.utc).date().isoformat(),
    }
    return storage.register_library_asset(payload, preserve_existing=True)


def create_library_upload(storage: Storage, library_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    metadata = normalize_upload_metadata(data)
    task_id = str(data.get("task_id") or "").strip()
    shot_id = str(data.get("shot_id") or "").strip()
    if task_id or shot_id:
        if not task_id or not shot_id:
            raise ValueError("task_id and shot_id must be provided together")
        task = storage.get_task(task_id)
        if not task:
            raise KeyError("Task does not exist")
        if task.get("status") not in {"draft", "edited", "failed"}:
            raise ValueError("Only a draft task can be edited")
        shot = next((item for item in task.get("shots", []) if item.get("id") == shot_id), None)
        if not shot:
            raise ValueError("shot_id does not belong to this task")
        if knowledge.canonical_city(task["input"]["city"]) != metadata["city"]:
            raise ValueError("library asset does not match this task city")
        if shot.get("entity_id") and shot["entity_id"] != metadata["entity_id"]:
            raise ValueError("library asset does not match this shot entity")
        if shot.get("shot_tag") and shot["shot_tag"] not in metadata.get("shot_tags", []):
            raise ValueError("library asset does not match this shot tag")
    encoded = data.get("data_base64")
    if not isinstance(encoded, str) or not encoded or len(encoded) > 30_000_000:
        raise ValueError("A non-empty media file up to 20MB is required")
    try:
        content = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
    except ValueError as exc:
        raise ValueError("data_base64 is invalid") from exc
    name = str(data.get("name") or "upload.bin").replace("\\", "/").rsplit("/", 1)[-1]
    asset = save_library_upload(storage, library_dir, name, content, metadata)
    if task_id:
        storage.append_asset(task_id, vars(library_asset_record(task_id, shot_id, asset)))
        return {**asset, "task_id": task_id, "shot_id": shot_id}
    return asset
