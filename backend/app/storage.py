from __future__ import annotations

import json
import sqlite3
import threading
import hashlib
from contextlib import closing
from pathlib import Path
from typing import Any

from .models import Task, now_iso


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

    def register_library_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(asset.get("local_path", "")))
        if not path.is_file():
            raise ValueError("library asset local_path does not exist")
        asset = dict(asset)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        asset.setdefault("id", f"library_{digest[:16]}")
        asset.setdefault("sha256", digest)
        asset.setdefault("created_at", now_iso())
        asset["updated_at"] = now_iso()
        with self._lock, closing(self._connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO library_assets(id,payload,created_at,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (asset["id"], json.dumps(asset, ensure_ascii=False), asset["created_at"], asset["updated_at"]),
                )
        return asset

    def list_library_assets(self, asset_type: str = "", query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT payload FROM library_assets ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        items = [json.loads(row["payload"]) for row in rows]
        q = query.strip().lower()
        return [item for item in items if (not asset_type or item.get("asset_type") == asset_type) and (not q or q in json.dumps(item, ensure_ascii=False).lower())]

    def find_library_asset(self, asset_type: str, query: str = "") -> dict[str, Any] | None:
        items = self.list_library_assets(asset_type, query, 500)
        return items[0] if items else None

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
                "license_status": "pending-review", "usage_scope": "technical-validation",
            })
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
                payload.setdefault("assets", []).append(asset)
                shot_id = asset.get("shot_id", "")
                if shot_id and asset.get("local_path"):
                    for shot in payload.get("shots", []):
                        if shot.get("id") == shot_id:
                            shot["asset_id"] = asset["id"]
                            shot["asset_url"] = asset.get("url", "")
                            shot["asset_local_path"] = asset["local_path"]
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
