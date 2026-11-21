from __future__ import annotations

import json
import sqlite3
import threading
import hashlib
from datetime import date, datetime, timezone
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .models import Task, now_iso
from .providers.contract import AssetProvenance, LIBRARY_REVIEW_STATES, MEDIA_TYPES, SHOT_TAGS, VISUAL_USAGES


def _file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _iso_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if value != parsed.isoformat():
        raise ValueError("Date must use YYYY-MM-DD")
    return parsed


def _publication_ready(item: dict[str, Any], today: date) -> bool:
    usages = item.get("allowed_usages")
    if (
        item.get("asset_type") not in MEDIA_TYPES
        or item.get("review_status") != "approved"
        or item.get("license_status") != "approved"
        or not isinstance(usages, list) or "publication" not in usages
        or not all(str(item.get(key) or "").strip() for key in ("license", "author", "source_url"))
        or not any(str(item.get(key) or "").strip() for key in ("license_url", "license_evidence"))
    ):
        return False
    expires = item.get("valid_until")
    try:
        return expires in (None, "") or _iso_date(expires) >= today
    except (TypeError, ValueError):
        return False


def validate_library_metadata(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate mutable library metadata and return a normalized copy."""
    normalized = dict(patch)
    if "review_status" in normalized and normalized["review_status"] not in LIBRARY_REVIEW_STATES:
        raise ValueError(f"review_status must be one of {LIBRARY_REVIEW_STATES}")
    if "license_status" in normalized and normalized["license_status"] not in {"pending", "approved", "rejected"}:
        raise ValueError("license_status must be pending, approved, or rejected")
    if "allowed_usages" in normalized and (
        not isinstance(normalized["allowed_usages"], list)
        or any(item not in VISUAL_USAGES for item in normalized["allowed_usages"])
        or len(set(normalized["allowed_usages"])) != len(normalized["allowed_usages"])
    ):
        raise ValueError(f"allowed_usages must contain values from {VISUAL_USAGES}")
    if "shot_tags" in normalized and (
        not isinstance(normalized["shot_tags"], list)
        or any(item not in SHOT_TAGS for item in normalized["shot_tags"])
        or len(set(normalized["shot_tags"])) != len(normalized["shot_tags"])
    ):
        raise ValueError(f"shot_tags must contain values from {tuple(SHOT_TAGS)}")
    for field in ("valid_until", "retrieved_at", "checked_at"):
        if field in normalized and normalized[field] not in (None, ""):
            normalized[field] = _iso_date(str(normalized[field])).isoformat()
    for field in ("source_url", "license_url"):
        value = normalized.get(field)
        if value:
            if not isinstance(value, str):
                raise ValueError(f"{field} must be an HTTP(S) URL")
            parsed = urlsplit(value)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise ValueError(f"{field} must be an HTTP(S) URL")
    return normalized


def library_provenance(item: dict[str, Any]) -> AssetProvenance:
    evidence = {key: item[key] for key in AssetProvenance.__annotations__ if key in item}
    evidence["library_asset_id"] = item["id"]
    if item.get("is_ai_generated"):
        evidence["classification"] = "ai-generated"
    elif item.get("entity_id") and _publication_ready(item, datetime.now(timezone.utc).date()):
        evidence["classification"] = "entity-approved"
    else:
        evidence["classification"] = item.get("classification") or "external-candidate"
    return evidence


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        progress INTEGER NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS assets (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS library_assets (
                        id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )"""
                )

    def register_library_asset(self, asset: dict[str, Any], *, preserve_existing: bool = False) -> dict[str, Any]:
        asset = validate_library_metadata(asset)
        path = Path(str(asset.get("local_path", "")))
        if not path.is_file():
            raise ValueError("library asset local_path does not exist")
        asset = dict(asset)
        digest = _file_sha256(path)
        asset.setdefault("id", f"library_{digest[:16]}")
        asset["sha256"] = digest
        asset.setdefault("review_status", "pending")
        asset.setdefault("license_status", "pending")
        asset.setdefault("allowed_usages", ["preview"])
        asset.setdefault("shot_tags", [])
        asset.setdefault("created_at", now_iso())
        asset["updated_at"] = now_iso()
        conflict = "DO NOTHING" if preserve_existing else "DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at"
        with self._lock, closing(self._connect()) as conn:
            with conn:
                changed = conn.execute(
                    f"INSERT INTO library_assets(id,payload,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(id) {conflict}",
                    (asset["id"], json.dumps(asset, ensure_ascii=False), asset["created_at"], asset["updated_at"]),
                ).rowcount
                if not changed:
                    row = conn.execute("SELECT payload FROM library_assets WHERE id = ?", (asset["id"],)).fetchone()
                    return json.loads(row["payload"])
        return asset

    def get_library_asset(self, asset_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_library_assets(
        self, asset_type: str = "", query: str = "", limit: int = 200,
        *, entity_id: str = "", shot_tag: str = "", review_status: str = "",
        city: str = "", usage: str = "preview", now: str | None = None, asset_id: str = "",
    ) -> list[dict[str, Any]]:
        if usage not in VISUAL_USAGES:
            raise ValueError(f"usage must be one of {VISUAL_USAGES}")
        today = _iso_date(now) if now else datetime.now(timezone.utc).date()
        q = query.strip().casefold()
        city = city.strip().removesuffix("市")
        limit = max(1, min(limit, 500))
        selected: list[dict[str, Any]] = []
        with closing(self._connect()) as conn:
            if asset_id:
                rows = conn.execute("SELECT payload FROM library_assets WHERE id = ?", (asset_id,))
            else:
                rows = conn.execute("SELECT payload FROM library_assets ORDER BY updated_at DESC, id")
            for row in rows:
                item = json.loads(row["payload"])
                if asset_type and item.get("asset_type") != asset_type:
                    continue
                if entity_id and item.get("entity_id") != entity_id:
                    continue
                if city and str(item.get("city") or "").strip().removesuffix("市") != city:
                    continue
                tags = item.get("shot_tags")
                if shot_tag and (not isinstance(tags, list) or shot_tag not in tags):
                    continue
                if review_status and item.get("review_status", "pending") != review_status:
                    continue
                if usage == "publication":
                    path = Path(str(item.get("local_path") or ""))
                    if not _publication_ready(item, today) or not path.is_file() or not self.library_file_matches(item, path):
                        continue
                if q and q not in json.dumps(item, ensure_ascii=False).casefold():
                    continue
                selected.append(item)
                if len(selected) == limit:
                    break
        return selected

    def find_library_asset(
        self, asset_type: str, query: str = "", *, entity_id: str = "",
        shot_tag: str = "", usage: str = "preview", now: str | None = None, asset_id: str = "",
    ) -> dict[str, Any] | None:
        items = self.list_library_assets(
            asset_type, query, 1, entity_id=entity_id, shot_tag=shot_tag,
            usage=usage, now=now, asset_id=asset_id,
        )
        return items[0] if items else None

    @staticmethod
    def library_file_matches(item: dict[str, Any], path: Path) -> bool:
        try:
            return path.resolve() == Path(item["local_path"]).resolve() and _file_sha256(path) == item["sha256"]
        except (KeyError, OSError):
            return False

    def update_library_asset(self, asset_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "entity_id", "city", "district", "place_name", "shot_tags", "source_url",
            "source_title", "source_publisher", "author", "license", "license_url",
            "license_evidence", "license_status", "allowed_usages", "usage_scope",
            "retrieved_at", "checked_at", "valid_until", "review_status", "classification",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"Unsupported library asset fields: {', '.join(sorted(unknown))}")
        patch = validate_library_metadata(patch)
        with self._lock, closing(self._connect()) as conn:
            with conn:
                row = conn.execute("SELECT payload FROM library_assets WHERE id = ?", (asset_id,)).fetchone()
                if not row:
                    raise KeyError("Library asset does not exist")
                item = json.loads(row["payload"])
                evidence_fields = {"entity_id", "city", "district", "place_name", "shot_tags", "source_url", "source_title",
                                   "source_publisher", "author", "license", "license_url", "license_evidence", "usage_scope", "valid_until"}
                evidence_changed = any(key in evidence_fields and item.get(key) != value for key, value in patch.items())
                item.update(patch)
                if evidence_changed and patch.get("review_status") != "approved":
                    if item.get("review_status") != "rejected":
                        item["review_status"] = "pending"
                    item.update(license_status="pending", allowed_usages=["preview"])
                    item.pop("checked_at", None)
                if any(key in patch for key in ("entity_id", "city")):
                    from .services.knowledge import canonical_city, get_entity
                    item["city"] = canonical_city(item.get("city") or "")
                    entity = get_entity(str(item.get("entity_id") or ""), item["city"]) if item.get("entity_id") else None
                    if item.get("entity_id") and not entity:
                        raise ValueError("entity_id is not in the selected city knowledge base")
                    if entity:
                        item["city"] = entity["city"]
                publishing = item.get("review_status") == "approved" and "publication" in item.get("allowed_usages", [])
                if publishing and not ("review_status" in patch or "allowed_usages" in patch):
                    publishing = _publication_ready(item, datetime.now(timezone.utc).date())
                    if not publishing:
                        item.update(review_status="pending", license_status="pending", allowed_usages=["preview"])
                if publishing:
                    from .services.knowledge import get_entity
                    if not get_entity(str(item.get("entity_id") or ""), str(item.get("city") or "")) or not item.get("shot_tags"):
                        raise ValueError("Publication assets require a valid knowledge entity and shot tags")
                    if not _publication_ready(item, datetime.now(timezone.utc).date()):
                        raise ValueError("Publication approval requires complete, unexpired license evidence")
                    if not self.library_file_matches(item, Path(str(item.get("local_path") or ""))):
                        raise ValueError("The library file has changed; publication approval was refused")
                if patch.get("review_status") == "approved":
                    item["checked_at"] = datetime.now(timezone.utc).date().isoformat()
                item["classification"] = (
                    "ai-generated" if item.get("is_ai_generated")
                    else "entity-approved" if publishing else "external-candidate"
                )
                item["updated_at"] = now_iso()
                conn.execute(
                    "UPDATE library_assets SET payload = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(item, ensure_ascii=False), item["updated_at"], asset_id),
                )
        return item

    def scan_library(self, media_root: Path) -> int:
        supported = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".ppm", ".mp4", ".mov", ".m4v", ".webm", ".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
        audio = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
        video = {".mp4", ".mov", ".m4v", ".webm"}
        count = 0
        if not media_root.exists():
            return 0
        for path in media_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in supported:
                continue
            kind = "audio" if path.suffix.lower() in audio else ("video" if path.suffix.lower() in video else "image")
            relative = path.relative_to(media_root).as_posix()
            self.register_library_asset({
                "local_path": str(path), "name": path.name, "asset_type": kind,
                "url": f"/media/{relative}", "source": "local-library-scan",
                "provider": "local-library", "is_ai_generated": path.suffix.lower() == ".ppm" or "offline" in path.name,
                "license_status": "pending", "usage_scope": "technical-validation",
            }, preserve_existing=True)
            count += 1
        return count

    def save_task(self, task: Task) -> None:
        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        with self._lock, closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO tasks(id, status, stage, progress, payload, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status,
                        stage=excluded.stage,
                        progress=excluded.progress,
                        payload=excluded.payload,
                        updated_at=excluded.updated_at
                    """,
                    (task.id, task.status, task.stage, task.progress, payload, task.created_at, task.updated_at),
                )
                conn.execute("DELETE FROM assets WHERE task_id = ?", (task.id,))
                for asset in task.assets:
                    data = json.dumps(asset, ensure_ascii=False) if isinstance(asset, dict) else json.dumps(asset.__dict__, ensure_ascii=False)
                    created_at = asset.get("created_at", task.updated_at) if isinstance(asset, dict) else asset.created_at
                    conn.execute(
                        "INSERT OR REPLACE INTO assets(id, task_id, payload, created_at) VALUES(?, ?, ?, ?)",
                        (asset["id"] if isinstance(asset, dict) else asset.id, task.id, data, created_at),
                    )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT payload FROM tasks ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 200)),)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def append_asset(self, task_id: str, asset: dict[str, Any]) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            with conn:
                row = conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if not row:
                    raise KeyError("任务不存在")
                payload = json.loads(row["payload"])
                payload["assets"] = [item for item in payload.get("assets", []) if item.get("shot_id") != asset.get("shot_id") or not asset.get("shot_id")]
                payload["assets"].append(asset)
                shot_id = asset.get("shot_id", "")
                if shot_id and asset.get("local_path"):
                    provenance = asset.get("provenance") if isinstance(asset.get("provenance"), dict) else {}
                    for shot in payload.get("shots", []):
                        if shot.get("id") == shot_id:
                            shot["asset_id"] = asset["id"]
                            shot["asset_url"] = asset.get("url", "")
                            shot["asset_local_path"] = asset["local_path"]
                            shot["asset_media_type"] = asset.get("media_type", "")
                            shot["library_asset_id"] = provenance.get("library_asset_id", "")
                            if provenance.get("entity_id"):
                                shot["entity_id"] = provenance["entity_id"]
                            if provenance.get("shot_tags") and shot.get("shot_tag") not in provenance["shot_tags"]:
                                shot["shot_tag"] = provenance["shot_tags"][0]
                payload["updated_at"] = asset.get("created_at", payload.get("updated_at"))
                conn.execute(
                    "UPDATE tasks SET payload = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), payload["updated_at"], task_id),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO assets(id, task_id, payload, created_at) VALUES(?, ?, ?, ?)",
                    (asset["id"], task_id, json.dumps(asset, ensure_ascii=False), asset.get("created_at", payload["updated_at"])),
                )
        return asset

    def delete_task(self, task_id: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            with conn:
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
                conn.execute("DELETE FROM assets WHERE task_id = ?", (task_id,))

    def save_raw_task(self, payload: dict[str, Any]) -> None:
        updated_at = payload.get("updated_at") or now_iso()
        payload["updated_at"] = updated_at
        with self._lock, closing(self._connect()) as conn:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, stage = ?, progress = ?, payload = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        payload.get("status", "draft"),
                        payload.get("stage", "preview"),
                        int(payload.get("progress", 35)),
                        json.dumps(payload, ensure_ascii=False),
                        updated_at,
                        payload["id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError("Task does not exist")
