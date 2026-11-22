from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import threading
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlparse

from .config import settings
from .models import TaskInput
from .providers.contract import CONTRACT_VERSION, SHOT_TAGS
from .providers.media_ffmpeg import validate_audio_file
from .providers.tts import generate_system_speech
from .services.catalog import bank_voices, get_catalog, get_music_library, resolve_music_track
from .services.audio_sync import _atomic_normalize
from .services.cost import summarize_cost
from .services.music_library import import_music, list_music_library
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
from .services.provider_settings import public_settings, uses_local_speech, validate_changes, write_dotenv
from .services.subject_review import save_reference
from .services.uploads import validate_audio_upload
from .services import knowledge
from .services.task_ledger import cost_ledger, reconcile, task_ledger
from .services.visual_review import read_review, record_review
from .storage import Storage, TaskBusyError


settings.ensure_dirs()
storage = Storage(settings.db_path)
storage.scan_library(settings.media_dir)
orchestrator = Orchestrator(settings, storage)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
PROVIDER_SETTINGS_PATH = PROJECT_ROOT / ".env"
PROVIDER_SETTINGS_ROUTE = "/api/provider-settings"
CURRENT_PROVIDER_SETTINGS_ROUTE = f"{PROVIDER_SETTINGS_ROUTE}/current"
MUSIC_LIBRARY_ROUTE = "/api/music-library"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_provider_settings_lock = threading.RLock()
_active_provider_posts = 0


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
        if unquote(urlparse(self.path).path) in {PROVIDER_SETTINGS_ROUTE, CURRENT_PROVIDER_SETTINGS_ROUTE, MUSIC_LIBRARY_ROUTE}:
            self._json(405, {"error": "仅支持本机同源访问"})
            return
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
            if path == PROVIDER_SETTINGS_ROUTE:
                if self._allow_provider_settings():
                    self._json(200, public_settings(orchestrator.settings))
                return
            if path == MUSIC_LIBRARY_ROUTE:
                if self._allow_provider_settings():
                    active = orchestrator.settings
                    self._json(200, list_music_library(active.music_catalog_path, active.music_dir))
                return
            if path == "/api/health":
                current = orchestrator
                active = current.settings
                system_config = current.system_config()
                self._json(
                    200,
                    {
                        "ok": True,
                        "service": "zhejiang-tourism-aigc",
                        "provider": active.default_provider,
                        "model": active.llm_model or "offline",
                        "asset_provider": active.asset_provider,
                        "tts_provider": active.tts_provider,
                        "contract_version": CONTRACT_VERSION,
                        "timing": "measured-per-shot-cues",
                        "synthetic_audio_fallback": active.allow_synthetic_audio_fallback,
                        "pipeline": system_config["pipeline"],
                    },
                )
                return
            if path == "/api/config":
                self._json(200, orchestrator.system_config())
                return
            if path == "/api/knowledge":
                self._json(200, knowledge.stats())
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
            if path == "/api/trash":
                self._json(200, {"items": storage.list_trashed_tasks()})
                return
            if path.startswith("/api/tasks/") and path.count("/") == 4 and path.endswith(("/ledger", "/cost", "/visual-review")):
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if not task:
                    self._json(404, {"error": "TASK_NOT_FOUND", "message": "Task does not exist"})
                else:
                    directory = settings.media_dir / task_id
                    if path.endswith("/ledger"):
                        self._send(200, json_bytes(task_ledger(task, directory, orchestrator.settings.music_catalog_path)),
                                   headers={"Content-Disposition": f'attachment; filename="{task_id}-ledger.json"'})
                    elif path.endswith("/visual-review"):
                        self._json(200, read_review(directory) if task.get("status") == "completed" else None)
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
                    directory = settings.media_dir / task_id
                    task["metrics"] = summarize_task_metrics(task)
                    billing = cost_ledger(task, directory)
                    task["cost"] = {**summarize_cost(task), "billing": billing}
                    if task.get("status") == "completed":
                        task["visual_review"] = read_review(directory)
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
            if path == "/lake-hero.webp":
                self._serve_file(FRONTEND_ROOT / "lake-hero.webp", "image/webp")
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
        global _active_provider_posts
        path = unquote(urlparse(self.path).path)
        if path == PROVIDER_SETTINGS_ROUTE:
            if self._allow_provider_settings(write=True):
                self._save_provider_settings()
            return
        if path == CURRENT_PROVIDER_SETTINGS_ROUTE:
            if self._allow_provider_settings(write=True):
                # This read-only POST carries no input fields, but consume its bounded body.
                try:
                    if not 0 <= int(self.headers.get("Content-Length", "0")) <= 1024:
                        raise ValueError("配置读取请求无效")
                    self._read_json()
                except ValueError:
                    self._json(400, {"error": "配置读取请求无效"})
                    return
                self._json(200, public_settings(orchestrator.settings, include_keys=True))
            return
        with _provider_settings_lock:
            _active_provider_posts += 1
        try:
            self._handle_post(path)
        finally:
            with _provider_settings_lock:
                _active_provider_posts -= 1

    def _handle_post(self, path: str) -> None:
        try:
            if path.startswith("/api/tasks/") and path.count("/") == 4 and path.endswith(("/visual-review", "/cost")):
                if not self._allow_provider_settings(write=True):
                    return
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if task is None:
                    raise KeyError("任务不存在")
                directory = settings.media_dir / task_id
                payload = self._read_json()
                if path.endswith("/cost"):
                    self._json(200, reconcile(task, directory, payload))
                elif task.get("status") == "completed":
                    self._json(200, record_review(directory, payload))
                else:
                    raise ValueError("请在成片完成后核验")
                return
            if path.startswith("/api/tasks/") and path.endswith("/subject-reference"):
                task_id = path.split("/")[3]
                task = storage.get_task(task_id)
                if not task or task.get("status") not in {"draft","edited","failed"}:
                    raise ValueError("请选择可编辑草稿")
                key = save_reference(settings.media_dir/task_id/"references", self._read_json().get("data_base64",""))
                self._json(201, {"reference": key})
                return
            if path == "/v1/audio/speech" and uses_local_speech(orchestrator.settings):
                self._local_speech()
                return
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
            if path.startswith("/api/trash/") and path.count("/") == 3:
                task_id = path.split("/")[3]
                storage.restore_task(task_id)
                self._json(200, {"id": task_id})
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
            if path == MUSIC_LIBRARY_ROUTE:
                if self._allow_provider_settings(write=True):
                    active = orchestrator.settings
                    track = import_music(self._read_json(), active.music_catalog_path, active.music_dir)
                    self._json(201, track)
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

    def do_DELETE(self) -> None:
        path = unquote(urlparse(self.path).path)
        if not path.startswith("/api/tasks/") or path.count("/") != 3:
            self._json(404, {"error": "NOT_FOUND", "message": "Endpoint does not exist"})
            return
        task_id = path.rsplit("/", 1)[1]
        try:
            trashed_at = orchestrator.trash_task(task_id)
            self._json(200, {"id": task_id, "trashed_at": trashed_at})
        except TaskBusyError as exc:
            self._json(409, {"error": "TASK_BUSY", "message": str(exc)})
        except KeyError:
            self._json(404, {"error": "TASK_NOT_FOUND", "message": "任务不存在"})
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

    def _allow_provider_settings(self, *, write: bool = False) -> bool:
        host = self.headers.get("Host", "")
        try:
            address = urlparse(f"http://{host}")
            local_host = address.hostname in LOOPBACK and address.port == self.server.server_port
            local_host = local_host and host.lower() in {
                f"127.0.0.1:{address.port}", f"localhost:{address.port}", f"[::1]:{address.port}",
            }
        except ValueError:
            local_host = False
        origin = self.headers.get("Origin")
        if (self.client_address[0] not in LOOPBACK or not local_host
                or (origin and origin.lower() != f"http://{host.lower()}")):
            self._json(403, {"error": "此功能仅供本机同源页面访问"})
            return False
        if write and self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "请通过配置页面提交 JSON"})
            return False
        return True

    def _save_provider_settings(self) -> None:
        global orchestrator
        try:
            if not 0 < int(self.headers.get("Content-Length", "0")) <= 65_536:
                raise ValueError("配置数据长度无效")
            submitted = self._read_json()
            with _provider_settings_lock:
                current = orchestrator.settings
                updates = validate_changes(current, submitted)
                if updates and (_active_provider_posts or orchestrator.has_running_tasks()):
                    self._json(409, {"error": "有任务正在运行，请完成后再保存配置。"})
                    return
                if updates:
                    new_settings = replace(current, **{name.lower(): value for name, value in updates.items()})
                    previous_env = {name: os.environ.get(name) for name in updates}
                    os.environ.update(updates)
                    try:
                        replacement = Orchestrator(new_settings, storage)
                        write_dotenv(PROVIDER_SETTINGS_PATH, updates)
                    except Exception:
                        for name, value in previous_env.items():
                            if value is None:
                                os.environ.pop(name, None)
                            else:
                                os.environ[name] = value
                        raise
                    orchestrator = replacement
                result = public_settings(orchestrator.settings)
            self._json(200, result)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            self._json(500, {"error": "配置未保存，请检查本机配置文件是否可写。"})

    def _local_speech(self) -> None:
        """OpenAI-compatible speech backed by the machine's installed SAPI voices."""
        active = orchestrator.settings
        expected = f"Bearer {active.tts_api_key}"
        authorization = self.headers.get("Authorization", "")
        if (self.client_address[0] not in {"127.0.0.1", "::1"} or not active.tts_api_key
                or not hmac.compare_digest(authorization.encode("utf-8"), expected.encode("utf-8"))):
            self._json(401, {"error": "Local speech requires a valid service token"})
            return
        if not 0 < int(self.headers.get("Content-Length", "0")) <= 16_384:
            raise ValueError("Invalid speech request length")
        data = self._read_json()
        text, voice = data.get("input"), data.get("voice")
        if (not isinstance(text, str) or not text.strip() or len(text) > 4096
                or not isinstance(voice, str) or voice not in bank_voices(active)):
            raise ValueError("Enter valid speech text and choose a configured Windows voice")
        if data.get("model") != active.tts_model or data.get("response_format", "wav") != "wav":
            raise ValueError("Select the configured local speech model and WAV output")
        speed = data.get("speed", 1.0)
        if type(speed) not in {int, float} or not 0.25 <= speed <= 4:
            raise ValueError("Speech speed must be between 0.25 and 4")
        with TemporaryDirectory(prefix="zj-local-tts-") as folder:
            raw = Path(folder) / "voice-raw.wav"
            output = Path(folder) / "voice.wav"
            generate_system_speech(text, raw, voice, speed, exact_voice=True)
            _atomic_normalize(raw, output)
            self._send(200, output.read_bytes(), "audio/wav")

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
        task = storage.get_task(task_id)
        if not task:
            raise KeyError("Task does not exist")
        shot_id = str(data.get("shot_id", ""))
        if task.get("status") not in {"draft", "edited", "failed"} or not any(shot["id"] == shot_id for shot in task.get("shots", [])):
            raise ValueError("请为可编辑的草稿选择有效镜头")
        fields = {key: str(data.get(key) or "").strip() for key in ("license", "author", "usage_scope", "grantor", "authorization_ref", "page_url")}
        if any(len(value) > 500 for value in fields.values()):
            raise ValueError("来源和授权字段每项最多 500 字")
        if fields["page_url"] and urlparse(fields["page_url"]).scheme not in {"http", "https"}:
            raise ValueError("素材来源链接需要使用 HTTP 或 HTTPS")
        try:
            decoded = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
        except ValueError as exc:
            raise ValueError("data_base64 is invalid") from exc
        target_dir = settings.media_dir / task_id / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        fingerprint = hashlib.sha256(task_id.encode() + b":" + shot_id.encode() + b":" + decoded).hexdigest()[:16]
        target = target_dir / f"{fingerprint}-{self._safe_filename(name, 'upload.bin')}"
        target.write_bytes(decoded)
        created_at = datetime.now(timezone.utc).isoformat()
        asset = {
            "id": f"upload_{fingerprint}",
            "task_id": task_id,
            "shot_id": shot_id,
            "name": name,
            "url": f"/media/{task_id}/uploads/{target.name}",
            "source": "local-upload",
            **{key: fields[key] or "待团队确认" for key in ("license", "author", "usage_scope")},
            **{key: fields[key] for key in ("grantor", "authorization_ref", "page_url")},
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
                "title": name,
                "path": str(target),
                "url": url,
                "moods": contract["moods"],
                "styles": contract["styles"],
                **{k: contract[k] for k in ("license", "author", "usage_scope", "approved", "authorization_basis", "authorization_ref")},
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
    # 进程重启后把遗留的 running 任务标记为失败（可从阶段重试），避免前端永远转圈。
    try:
        orchestrator.recover_interrupted_tasks()
    except Exception as exc:  # noqa: BLE001 - 恢复失败不阻塞启动
        print(f"Interrupted-task recovery skipped: {exc}")
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
