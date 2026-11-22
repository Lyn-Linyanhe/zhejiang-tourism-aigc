"""Idempotently migrate active runtime paths after the competition root rename.

Only SQLite payloads and active runtime JSON/concat files are rewritten. Frozen
comparison inputs, PPT evidence, validation logs, and historical deliverables are
read-only; their hashes are checked against ``new/pre-rename-inventory.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OLD_COMPETITION_ROOTS = (
    r"C:\Users\Lenovo\Desktop\人工智能赛",
    r"C:\Users\ev041\Desktop\人工智能赛",
    r"C:\Users\ev041\Desktop\AI competition",
)
PROJECT_MARKER = "zhejiang-tourism-aigc"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class MigrationStats:
    sqlite_rows: int = 0
    runtime_files: int = 0
    replacements: int = 0


class PathMapper:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.competition_root = self.project_root.parent
        self.replacements = 0

    def _candidate(self, raw: str) -> Path | None:
        normalized = raw.replace("/", "\\")
        lowered = normalized.lower()
        current_project = str(self.project_root).lower()
        current_competition = str(self.competition_root).lower()
        if (
            lowered == current_project
            or lowered.startswith(current_project + "\\")
            or lowered == current_competition
        ):
            return None
        for old_root in OLD_COMPETITION_ROOTS:
            prefix = old_root.lower()
            if lowered == prefix or lowered.startswith(prefix + "\\"):
                relative = normalized[len(old_root) :].lstrip("\\")
                return self.competition_root / Path(relative)

        marker = f"\\{PROJECT_MARKER}\\"
        marker_index = lowered.find(marker.lower())
        if marker_index >= 0:
            suffix = normalized[marker_index + len(marker) :]
            return self.project_root / Path(suffix)
        if lowered.endswith(f"\\{PROJECT_MARKER}".lower()):
            return self.project_root
        return None

    def map_string(self, value: str) -> str:
        candidate = self._candidate(value)
        if candidate is None:
            return value
        # Only normalize references whose destination can be proven to exist.
        if not candidate.exists():
            return value
        mapped = str(candidate)
        if value == mapped:
            return value
        self.replacements += 1
        return mapped

    def map_tree(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.map_tree(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.map_tree(item) for item in value]
        if isinstance(value, str):
            return self.map_string(value)
        return value

    def map_concat_text(self, text: str) -> str:
        pattern = re.compile(r"(?P<prefix>file\s+['\"])(?P<path>[^'\"]+)(?P<suffix>['\"])")

        def replace(match: re.Match[str]) -> str:
            original = match.group("path").replace("'\\''", "'")
            mapped = self.map_string(original)
            if mapped == original:
                return match.group(0)
            escaped = Path(mapped).as_posix().replace("'", "'\\''")
            return f"{match.group('prefix')}{escaped}{match.group('suffix')}"

        return pattern.sub(replace, text)


def migrate_sqlite(db_path: Path, mapper: PathMapper) -> int:
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {db_path}")
    changed = 0
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in ("tasks", "assets", "library_assets"):
            rows = connection.execute(f"SELECT rowid, payload FROM {table}").fetchall()
            for rowid, payload_text in rows:
                payload = json.loads(payload_text)
                migrated = mapper.map_tree(payload)
                serialized = json.dumps(migrated, ensure_ascii=False)
                if serialized != payload_text:
                    connection.execute(
                        f"UPDATE {table} SET payload = ? WHERE rowid = ?",
                        (serialized, rowid),
                    )
                    changed += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return changed


def migrate_runtime_files(project_root: Path, mapper: PathMapper) -> int:
    media_root = project_root / "runtime" / "media"
    if not media_root.exists():
        return 0
    changed = 0
    for path in sorted(media_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            original = path.read_text(encoding="utf-8")
            data = json.loads(original)
            migrated = mapper.map_tree(data)
            if migrated == data:
                continue
            updated = json.dumps(migrated, ensure_ascii=False, indent=2) + "\n"
        elif path.name == "concat.txt":
            original = path.read_text(encoding="utf-8")
            updated = mapper.map_concat_text(original)
        else:
            continue
        if updated != original:
            temp = path.with_name(path.name + ".migration.tmp")
            temp.write_text(updated, encoding="utf-8")
            os.replace(temp, path)
            changed += 1
    return changed


def verify_protected_files(competition_root: Path, inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    checked: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for record in inventory.get("protected_delivery_files", []):
        path = competition_root / Path(record["path"])
        actual = sha256(path) if path.is_file() else "missing"
        item = {
            "path": record["path"],
            "expected_sha256": record["sha256"],
            "actual_sha256": actual,
            "match": actual == record["sha256"],
        }
        checked.append(item)
        if not item["match"]:
            mismatches.append(item)
    return {"checked": checked, "mismatches": mismatches, "ok": not mismatches}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    competition_root = project_root.parent
    expected_root_name = "AI competition"
    if competition_root.name != expected_root_name:
        raise RuntimeError(
            f"Refusing to migrate outside the renamed root {expected_root_name!r}: "
            f"{competition_root}"
        )

    inventory_path = project_root / "new" / "pre-rename-inventory.json"
    before = verify_protected_files(competition_root, inventory_path)
    if not before["ok"]:
        raise RuntimeError("Protected delivery hashes do not match before migration")

    mapper = PathMapper(project_root)
    stats = MigrationStats()
    stats.sqlite_rows = migrate_sqlite(project_root / "runtime" / "db" / "app.sqlite3", mapper)
    stats.runtime_files = migrate_runtime_files(project_root, mapper)
    stats.replacements = mapper.replacements

    after = verify_protected_files(competition_root, inventory_path)
    if not after["ok"]:
        raise RuntimeError("Protected delivery hashes changed during migration")

    report = {
        "schema": "zj-path-migration-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project_root),
        "sqlite_rows_changed": stats.sqlite_rows,
        "runtime_files_changed": stats.runtime_files,
        "path_replacements": stats.replacements,
        "protected_files": after,
    }
    report_path = project_root / "new" / "path-migration-report.json"
    temp_report = report_path.with_suffix(".tmp")
    temp_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_report, report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
