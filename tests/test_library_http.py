"""HTTP regression tests with isolated storage and no external providers."""

import base64
import http.client
import importlib
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from backend.app.config import Settings
from backend.app.models import Shot, Task, TaskInput
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage


class LibraryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.import_dir = tempfile.TemporaryDirectory()
        isolated = Settings(data_dir=Path(cls.import_dir.name), audio_model_cache=Path(cls.import_dir.name) / "models", default_provider="offline")
        with patch("backend.app.config.settings", isolated):
            cls.app = importlib.import_module("backend.app.main")
        cls.addClassCleanup(cls.import_dir.cleanup)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(data_dir=Path(self.temp.name), audio_model_cache=Path(self.temp.name) / "models", default_provider="offline")
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.db_path)
        self.orchestrator = Orchestrator(self.settings, self.storage)
        for name, value in (("settings", self.settings), ("storage", self.storage), ("orchestrator", self.orchestrator)):
            context = patch.object(self.app, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.AppHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.task = Task(id="task_library_http", input=TaskInput.from_dict({"city": "杭州", "theme": "雷峰塔", "duration": 15}), status="draft", stage="preview")
        self.task.shots = [Shot("shot_1", 1, 15, "塔景", "全景", "旁白", "字幕", entity_id="hz-leifengta", shot_tag="wide")]
        self.storage.save_task(self.task)
        self.content = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX2kAAAAASUVORK5CYII=")

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if "json" in response.getheader("Content-Type", "") else body
        finally:
            connection.close()

    def upload(self, **changes):
        data = {
            "name": "tower.png", "data_base64": base64.b64encode(self.content).decode(),
            "entity_id": "hz-leifengta", "city": "杭州市", "shot_tags": ["wide"],
            "source_url": "https://example.org/tower", "source_title": "Tower photograph",
            "author": "Photographer", "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/", **changes,
        }
        return self.request("POST", "/api/library/assets", data)

    def approve(self, asset):
        return self.request("PATCH", f"/api/library/assets/{asset['id']}", {
            "review_status": "approved", "license_status": "approved", "allowed_usages": ["preview", "publication"],
        })

    def test_invalid_bindings_leave_no_files_or_records(self):
        cases = (
            (400, {"task_id": self.task.id}),
            (400, {"shot_id": "shot_1"}),
            (404, {"task_id": "missing", "shot_id": "shot_1"}),
            (400, {"task_id": self.task.id, "shot_id": "missing"}),
            (400, {"task_id": self.task.id, "shot_id": "shot_1", "entity_id": "hz-duanqiao"}),
            (400, {"task_id": self.task.id, "shot_id": "shot_1", "shot_tags": ["detail"]}),
        )
        for expected, data in cases:
            with self.subTest(data=data):
                status, body = self.upload(**data)
                self.assertEqual(status, expected, body)
                self.assertEqual(self.storage.list_library_assets(), [])
                self.assertEqual(list(self.settings.library_dir.rglob("*")), [])
                self.assertEqual(self.storage.get_task(self.task.id)["assets"], [])

    def test_new_upload_cannot_self_approve_and_city_is_inferred(self):
        status, asset = self.upload(city="", review_status="approved", license_status="approved", allowed_usages=["publication"])
        self.assertEqual(status, 201, asset)
        self.assertEqual(asset["city"], "杭州")
        self.assertEqual(asset["review_status"], "pending")
        self.assertEqual(asset["license_status"], "pending")
        self.assertEqual(asset["allowed_usages"], ["preview"])
        self.assertEqual(self.request("GET", asset["url"]), (200, self.content))
        status, listing = self.request("GET", "/api/library/assets?" + urlencode({"city": "杭州市", "usage": "preview"}))
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listing["items"]], [asset["id"]])

    def test_valid_binding_updates_shot_and_replaces_previous_record(self):
        status, asset = self.upload(task_id=self.task.id, shot_id="shot_1")
        self.assertEqual(status, 201, asset)
        self.upload(task_id=self.task.id, shot_id="shot_1")
        task = self.storage.get_task(self.task.id)
        self.assertEqual(task["shots"][0]["library_asset_id"], asset["id"])
        self.assertEqual(task["shots"][0]["asset_media_type"], "image")
        self.assertEqual(len(task["assets"]), 1)
        self.assertEqual(task["assets"][0]["provenance"]["sha256"], asset["sha256"])

    def test_running_task_rejects_upload_without_writing(self):
        self.task.status = "running"
        self.storage.save_task(self.task)
        status, body = self.upload(task_id=self.task.id, shot_id="shot_1")
        self.assertEqual(status, 400, body)
        self.assertEqual(self.storage.list_library_assets(), [])

    def test_publication_filter_review_and_duplicate_upload(self):
        _, asset = self.upload()
        path = "/api/library/assets?" + urlencode({"entity_id": "hz-leifengta", "shot_tag": "wide", "usage": "publication"})
        self.assertEqual(self.request("GET", path)[1]["items"], [])
        status, reviewed = self.approve(asset)
        self.assertEqual(status, 200, reviewed)
        self.assertTrue(reviewed["checked_at"])
        self.assertEqual(len(self.request("GET", path)[1]["items"]), 1)
        status, duplicate = self.upload(author="Unreviewed replacement")
        self.assertEqual(status, 201, duplicate)
        self.assertEqual(duplicate["author"], "Photographer")
        self.assertEqual(duplicate["review_status"], "approved")
        Path(asset["local_path"]).write_bytes(b"changed")
        self.assertEqual(self.request("GET", path)[1]["items"], [])
        self.assertEqual(self.approve(asset)[0], 400)

    def test_incomplete_or_expired_license_cannot_be_approved(self):
        _, asset = self.upload(author="")
        self.assertEqual(self.approve(asset)[0], 400)
        self.request("PATCH", f"/api/library/assets/{asset['id']}", {"author": "Photographer", "valid_until": "2020-01-01"})
        self.assertEqual(self.approve(asset)[0], 400)
        self.assertEqual(self.storage.get_library_asset(asset["id"])["review_status"], "pending")

    def test_patch_rejects_immutable_fields_and_malformed_values(self):
        _, asset = self.upload()
        for data in ({"local_path": "outside.png"}, {"sha256": "forged"}, {"shot_tags": ["invalid"]}, {"source_url": "https://"}, {"valid_until": "not-a-date"}):
            with self.subTest(data=data):
                self.assertEqual(self.request("PATCH", f"/api/library/assets/{asset['id']}", data)[0], 400)

    def test_clearing_library_binding_clears_reusable_file(self):
        _, asset = self.upload()
        endpoint = f"/api/tasks/{self.task.id}/shots/shot_1"
        self.assertEqual(self.request("PATCH", endpoint, {"library_asset_id": asset["id"]})[0], 200)
        status, task = self.request("PATCH", endpoint, {"library_asset_id": None, "entity_id": None, "shot_tag": None})
        self.assertEqual(status, 200, task)
        self.assertEqual(task["shots"][0]["asset_local_path"], "")
        self.assertEqual(task["assets"], [])

    def test_rejected_library_binding_cannot_be_saved(self):
        _, asset = self.upload()
        self.request("PATCH", f"/api/library/assets/{asset['id']}", {"review_status": "rejected"})
        status, body = self.request("PATCH", f"/api/tasks/{self.task.id}/shots/shot_1", {"library_asset_id": asset["id"]})
        self.assertEqual(status, 400, body)

    def test_invalid_usage_is_client_error_and_media_stays_within_root(self):
        self.assertEqual(self.request("GET", "/api/library/assets?usage=invalid")[0], 400)
        marker = self.settings.data_dir / "private.txt"
        marker.write_text("private", encoding="utf-8")
        for path in ("/library-media/../private.txt", "/library-media/%2e%2e/private.txt", "/media/%2e%2e/private.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
