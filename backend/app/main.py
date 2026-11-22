from __future__ import annotations

import base64
import json
import mimetypes
import os
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .config import settings
from .models import TaskInput
from .providers.contract import CONTRACT_VERSION, SHOT_TAGS
from .providers.media_ffmpeg import validate_audio_file
from .services.catalog import get_catalog, get_music_library, resolve_music_track
from .services.metrics import (
    MAX_BILLING_RECORDS_PER_TASK,
    MAX_OBSERVATIONS_PER_TASK,
    observation_key,
    summarize_task_metrics,
    validate_billing_record,
    validate_publication_observation,
)
from .services.knowledge import list_entities
from .services.library import create_library_upload
from .services.orchestrator import Orchestrator
from .services.script_export import export_script
from .services.uploads import validate_audio_upload
from .storage import Storage


settings.ensure_dirs()
storage = Storage(settings.db_path)
storage.scan_library(settings.media_dir)
orchestrator = Orchestrator(settings, storage)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


class AppHandler(BaseHTTPRequestHandler):
    server_version = "ZhejiangTourismAIGC/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        line = f"{self.address_string()} - {fmt % args}\n"
        with (settings.logs_dir / "access.log").open("a", encoding="utf-8") as log:
            log.write(line)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json_bytes(payload))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 35_000_000:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_OPTIONS(self) -> None:
        self._send(
            204,
            b"",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET,POST,PATCH,PUT,OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/health":
                system_config = orchestrator.system_config()
                self._json(
                    200,
                    {
                        "ok": True,
                        "service": "zhejiang-tourism-aigc",
                        "provider": settings.default_provider,
                        "model": settings.llm_model or "offline",
                        "asset_provider": settings.asset_provider,
                        "tts_provider": settings.tts_provider,
                        "contract_version": CONTRACT_VERSION,
                        "timing": "measured-per-shot-cues",
                        "synthetic_audio_fallback": settings.allow_synthetic_audio_fallback,
                        "pipeline": system_config["pipeline"],
                    },
                )
                return
            if path == "/api/config":
                self._json(200, orchestrator.system_config())
                return
            if path == "/api/catalog":
                payload = get_catalog()
                payload["entities"] = list_entities()
                payload["shot_tags"] = SHOT_TAGS
                self._json(200, payload)
                return
            if path == "/api/music/library":
                # 前端"浏览曲库"：只列已授权且文件存在的曲目
                self._json(
                    200,
                    get_music_library(settings.music_catalog_path, settings.music_dir),
                )
                return
            if path == "/api/tasks":
                limit = int(parse_qs(parsed.query).get("limit", ["50"])[0])
                items = storage.list_tasks(limit)
                for task in items:
                    task["metrics"] = summarize_task_metrics(task)
                self._json(
                    200,
                    {"items": items, "total": len(storage.list_tasks(200))},
                )
                return
            if path.startswith("/api/tasks/") and path.endswith("/cost"):
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if not task:
                    self._json(404, {"error": "TASK_NOT_FOUND", "message": "Task does not exist"})
                else:
                    self._json(200, summarize_task_metrics(task))
                return
            if path.startswith("/api/tasks/") and path.endswith("/export"):
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if not task:
                    self._json(404, {"error": "TASK_NOT_FOUND", "message": "Task does not exist"})
                    return
                fmt = parse_qs(parsed.query).get("format", ["md"])[0]
                body, content_type, filename = export_script(task, fmt)
                self._send(
                    200,
                    body,
                    content_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
                return
            if path == "/api/assets":
                task_id = parse_qs(parsed.query).get("task_id", [""])[0]
                task = storage.get_task(task_id) if task_id else None
                assets = task.get("assets", []) if task else []
                self._json(200, {"items": assets, "total": len(assets)})
                return
            if path == "/api/library/assets":
                query = parse_qs(parsed.query)
                items = storage.list_library_assets(
                    query.get("type", [""])[0], query.get("q", [""])[0],
                    entity_id=query.get("entity_id", [""])[0],
                    shot_tag=query.get("shot_tag", [""])[0],
                    review_status=query.get("review_status", [""])[0],
                    city=query.get("city", [""])[0], usage=query.get("usage", ["preview"])[0],
                )
                self._json(200, {"items": items, "total": len(items)})
                return
            if path.startswith("/api/tasks/"):
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if not task:
                    self._json(
                        404,
                        {"error": "TASK_NOT_FOUND", "message": "Task does not exist"},
                    )
                else:
                    task["metrics"] = summarize_task_metrics(task)
                    task["cost"] = task["metrics"]["cost"]
                    self._json(200, task)
                return
            if path.startswith("/library-media/"):
                self._serve_media(path)
                return
            if path.startswith("/music/"):
                # resolve_music_track 只放行 catalog 里已授权、且落在 music_dir 内的文件，
                # 这里不再套 _serve_file 的 PROJECT_ROOT 限制（曲库目录可以配到仓库外）。
                target = resolve_music_track(
                    path.removeprefix("/music/"),
                    settings.music_catalog_path,
                    settings.music_dir,
                )
                if target is None:
                    self._send(404, b"Not Found", "text/plain; charset=utf-8")
                    return
                self._send(
                    200,
                    target.read_bytes(),
                    mimetypes.guess_type(target.name)[0] or "audio/mpeg",
                    headers={"Content-Disposition": f'inline; filename="{target.name}"'},
                )
                return
            if path.startswith("/media/"):
                self._serve_media(path)
                return
            if path in {"/", "/index.html"}:
                self._serve_file(FRONTEND_ROOT / "index.html", "text/html; charset=utf-8")
                return
            if path in {"/app.js", "/voice-studio.js", "/styles.css"}:
                self._serve_file(
                    FRONTEND_ROOT / path.lstrip("/"),
                    mimetypes.guess_type(path)[0] or "text/plain",
                )
                return
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
        except ValueError as exc:
            self._json(400, {"error": "INVALID_REQUEST", "message": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "INTERNAL_ERROR", "message": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path == "/api/previews":
                task = orchestrator.create_preview(
                    TaskInput.from_dict(self._read_json())
                )
                self._json(200, task.to_dict())
                return
            if path.startswith("/api/tasks/") and path.endswith(("/voice-plan", "/voice-preview")):
                task_id = path.split("/")[3]
                self._json(200, orchestrator.voice_preview(task_id, self._read_json(), synthesize=path.endswith("/voice-preview")))
                return
            if path == "/api/tasks":
                task = orchestrator.create_task(
                    TaskInput.from_dict(self._read_json())
                )
                self._json(202, task.to_dict())
                return
            if path.startswith("/api/tasks/") and path.endswith("/generate"):
                task_id = path.split("/")[3]
                self._json(202, orchestrator.generate(task_id))
                return
            if path.startswith("/api/tasks/") and path.endswith("/retry"):
                task_id = path.split("/")[3]
                data = self._read_json()
                self._json(
                    202,
                    orchestrator.retry(task_id, data.get("from_stage", "scripting")),
                )
                return
            if path == "/api/library/assets":
                self._json(201, create_library_upload(storage, settings.library_dir, self._read_json()))
                return
            if path == "/api/assets":
                self._create_asset()
                return
            if path == "/api/uploads":
                self._create_upload()
                return
            if path.startswith("/api/tasks/") and path.endswith("/billing"):
                task_id = path.split("/")[3]
                data = self._read_json()
                record = validate_billing_record(data)
                task = storage.get_task(task_id)
                if not task:
                    raise KeyError("Task does not exist")
                records = list(task.get("billing_records") or [])
                if len(records) >= MAX_BILLING_RECORDS_PER_TASK:
                    raise ValueError("Billing record limit reached")
                record["id"] = str(data.get("id") or f"bill_{int(datetime.now(timezone.utc).timestamp() * 1000)}")
                record["recorded_at"] = datetime.now(timezone.utc).isoformat()
                records.append(record)
                task["billing_records"] = records
                storage.save_raw_task(task)
                self._json(201, {"items": records, "by_currency": summarize_task_metrics(task)["billing"]["by_currency"]})
                return
            if path.startswith("/api/tasks/") and path.endswith("/publication-observations"):
                task_id = path.split("/")[3]
                record = validate_publication_observation(self._read_json())
                task = storage.get_task(task_id)
                if not task:
                    raise KeyError("Task does not exist")
                observations = list(task.get("publication_observations") or [])
                key = observation_key(record)
                existing_index = next((index for index, item in enumerate(observations) if observation_key(item) == key), None)
                if existing_index is None and len(observations) >= MAX_OBSERVATIONS_PER_TASK:
                    raise ValueError("Publication observation limit reached")
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                if existing_index is None:
                    observations.append(record)
                else:
                    previous = observations[existing_index]
                    history = list(previous.get("revisions") or [])
                    history.append({key: value for key, value in previous.items() if key != "revisions"})
                    record["revisions"] = history[-20:]
                    observations[existing_index] = record
                task["publication_observations"] = observations
                storage.save_raw_task(task)
                self._json(201, {"items": observations})
                return
            self._json(404, {"error": "NOT_FOUND", "message": "Endpoint does not exist"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "INVALID_REQUEST", "message": str(exc)})
        except KeyError as exc:
            self._json(404, {"error": "NOT_FOUND", "message": str(exc)})
        except Exception as exc:
            self._json(
                500,
                {
                    "error": "INTERNAL_ERROR",
                    "message": str(exc),
                    "trace": traceback.format_exc(limit=3),
                },
            )

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            if path.startswith("/api/library/assets/") and path.count("/") == 4:
                asset_id = path.split("/")[4]
                self._json(200, storage.update_library_asset(asset_id, self._read_json()))
                return
            if path.startswith("/api/tasks/") and path.count("/") == 3:
                task_id = path.split("/")[3]
                task = orchestrator.update_draft(task_id, self._read_json())
                task["metrics"] = summarize_task_metrics(task)
                task["cost"] = task["metrics"]["cost"]
                self._json(200, task)
                return
            if path.startswith("/api/tasks/") and "/shots/" in path:
                parts = path.split("/")
                task_id, shot_id = parts[3], parts[5]
                self._json(
                    200,
                    orchestrator.update_shot(task_id, shot_id, self._read_json()),
                )
                return
            self._json(404, {"error": "NOT_FOUND", "message": "Endpoint does not exist"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "INVALID_REQUEST", "message": str(exc)})
        except KeyError as exc:
            self._json(404, {"error": "NOT_FOUND", "message": str(exc)})
        except Exception as exc:
            self._json(500, {"error": "INTERNAL_ERROR", "message": str(exc)})

    def do_PUT(self) -> None:
        self._json(
            501,
            {"error": "NOT_IMPLEMENTED", "message": "Use PATCH for draft updates"},
        )

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.resolve().is_relative_to(PROJECT_ROOT.resolve()) or not path.exists():
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        self._send(200, path.read_bytes(), content_type)

    def _serve_media(self, path: str) -> None:
        if path.startswith("/media/"):
            relative = Path(*path.removeprefix("/media/").split("/"))
            target = (settings.media_dir / relative).resolve()
            root = settings.media_dir.resolve()
        elif path.startswith("/library-media/"):
            relative = Path(*path.removeprefix("/library-media/").split("/"))
            target = (settings.library_dir / relative).resolve()
            root = settings.library_dir.resolve()
        else:
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        if not target.is_relative_to(root) or not target.is_file():
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(
            200,
            target.read_bytes(),
            content_type,
            headers={"Content-Disposition": f'inline; filename="{target.name}"'},
        )

    @staticmethod
    def _safe_filename(name: str, fallback: str) -> str:
        safe = "".join(char for char in name if char.isalnum() or char in "._-")[:100]
        return safe or fallback

    def _create_asset(self) -> None:
        data = self._read_json()
        task_id = str(data.get("task_id", "")).strip()
        name = str(data.get("name", "upload.bin")).strip()
        encoded = str(data.get("data_base64", "")).strip()
        if not task_id or not encoded:
            raise ValueError("task_id and data_base64 are required")
        if len(encoded) > 30_000_000:
            raise ValueError("Media upload cannot exceed 20MB")
        if Path(name).suffix.lower() not in {
            ".jpg", ".jpeg", ".png", ".webp", ".bmp",
            ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv",
        }:
            raise ValueError("Only image and video assets are supported")
        if not storage.get_task(task_id):
            raise KeyError("Task does not exist")
        target_dir = settings.media_dir / task_id / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / self._safe_filename(name, "upload.bin")
        payload = encoded.split(",", 1)[1] if "," in encoded else encoded
        try:
            target.write_bytes(base64.b64decode(payload, validate=True))
        except Exception as exc:
            raise ValueError("data_base64 is invalid") from exc
        created_at = datetime.now(timezone.utc).isoformat()
        asset = {
            "id": f"upload_{target.stem}",
            "task_id": task_id,
            "shot_id": str(data.get("shot_id", "")),
            "name": name,
            "url": f"/media/{task_id}/uploads/{target.name}",
            "source": "local-upload",
            "license": data.get("license", "Pending team verification"),
            "author": data.get("author", "Pending team verification"),
            "usage_scope": data.get("usage_scope", "Pending team verification"),
            "is_ai_generated": False,
            "local_path": str(target),
            "created_at": created_at,
        }
        storage.append_asset(task_id, asset)
        self._json(201, asset)

    def _create_upload(self) -> None:
        data = self._read_json()
        task_id = str(data.get("task_id", "")).strip()
        kind = str(data.get("kind", "")).strip().lower()
        name = str(data.get("name", "upload.bin")).strip()
        encoded = str(data.get("data_base64", "")).strip()
        if not task_id or kind not in {"voice", "music"} or not encoded:
            raise ValueError("task_id, kind and data_base64 are required")
        if len(encoded) > 35_000_000:
            raise ValueError("Audio upload cannot exceed 25MB")
        task = storage.get_task(task_id)
        if not task:
            raise KeyError("Task does not exist")
        # P3：逐镜配音（voice 必须带 shot_id）与带授权字段的音乐上传
        contract = validate_audio_upload(data, task)
        target_dir = settings.media_dir / task_id / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"voice-{contract['shot_id']}" if kind == "voice" else "music"
        target = target_dir / f"{prefix}-{self._safe_filename(name, f'{kind}.bin')}"
        temporary = target.with_name(target.name + ".upload.tmp")
        payload = encoded.split(",", 1)[1] if "," in encoded else encoded
        try:
            temporary.write_bytes(base64.b64decode(payload, validate=True))
            validate_audio_file(temporary)
            os.replace(temporary, target)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            raise ValueError("data_base64 is invalid") from exc
        task.setdefault("audio", {})
        url = f"/media/{task_id}/uploads/{target.name}"
        if kind == "voice":
            for shot in task.get("shots", []):
                if shot.get("id") == contract["shot_id"]:
                    shot["audio_local_path"] = str(target)
                    shot["audio_url"] = url
                    break
        else:
            task["audio"]["music_upload"] = {
                "id": f"upload-{target.stem}",
                "path": str(target),
                "url": url,
                "moods": contract["moods"],
                "styles": contract["styles"],
                "license": contract["license"],
                "author": contract["author"],
                "usage_scope": contract["usage_scope"],
                "approved": True,
            }
            # 兼容字段：历史客户端仍读取 music_local_path / music_url
            task["audio"]["music_local_path"] = str(target)
            task["audio"]["music_url"] = url
            task["audio"]["music_provider"] = "task-upload"
        storage.save_raw_task(task)
        self._json(
            201,
            {
                "task_id": task_id,
                "kind": kind,
                "shot_id": contract.get("shot_id", ""),
                "name": name,
                "url": url,
            },
        )


def run() -> None:
    server = ThreadingHTTPServer((settings.host, settings.port), AppHandler)
    print(f"ZhejiangTourismAIGC listening on http://{settings.host}:{settings.port}")
    print(f"Frontend: {FRONTEND_ROOT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server")
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
