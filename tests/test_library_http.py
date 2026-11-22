"""HTTP regression tests with isolated storage and no external providers."""

import base64
import dataclasses
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
from backend.app.models import Shot, Task, TaskInput, TraceRecord
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

    def test_music_library_and_preview_only_serve_approved_tracks(self):
        music_dir = Path(self.temp.name) / "music"
        music_dir.mkdir()
        (music_dir / "keep.mp3").write_bytes(b"ID3fake-audio")
        (music_dir / "secret.txt").write_bytes(b"secret")
        catalog = music_dir / "catalog.json"
        catalog.write_text(json.dumps({
            "tracks": [
                {"id": "keep", "file": "keep.mp3", "moods": ["舒缓"],
                 "styles": ["诗意纪实"], "approved": True},
                {"id": "draft", "file": "keep.mp3", "moods": ["热烈"],
                 "styles": ["诗意纪实"], "approved": False},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        isolated = dataclasses.replace(self.settings, music_dir=music_dir, music_catalog_path=catalog)
        with patch.object(self.app, "settings", isolated):
            status, body = self.request("GET", "/api/music/library")
            self.assertEqual(status, 200, body)
            self.assertEqual(body["total"], 1)
            self.assertEqual([t["id"] for t in body["tracks"]], ["keep"])

            status, payload = self.request("GET", "/music/keep.mp3")
            self.assertEqual(status, 200)
            self.assertEqual(payload, b"ID3fake-audio")
            for path in ("/music/missing.mp3", "/music/secret.txt"):
                with self.subTest(path=path):
                    status, _ = self.request("GET", path)
                    self.assertEqual(status, 404)

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

    def test_batch_draft_save_is_atomic_over_http(self):
        self.task.shots = [Shot(f"shot_{i}", i, 3.75, "塔", "全景", "旁白", "字幕") for i in range(1, 5)]
        self.task.trace = TraceRecord("offline", "test", "v1", False, events=[{"type": "asset", "cost": 0.42, "cost_reported": True}])
        self.storage.save_task(self.task)
        endpoint = f"/api/tasks/{self.task.id}"
        status, task = self.request("PATCH", endpoint, {"title": "新标题", "shots": [
            {"id": "shot_1", "duration_sec": 4.25}, {"id": "shot_2", "duration_sec": 3.25},
        ]})
        self.assertEqual(status, 200, task)
        self.assertEqual([shot["duration_sec"] for shot in task["shots"]], [4.25, 3.25, 3.75, 3.75])
        self.assertEqual(task["cost"]["total"], 0.42)
        before = self.storage.get_task(self.task.id)
        status, error = self.request("PATCH", endpoint, {"title": "不应保存", "shots": [
            {"id": "shot_1", "duration_sec": 8}, {"id": "shot_2", "duration_sec": 3.25},
        ]})
        self.assertEqual(status, 400, error)
        self.assertEqual(self.storage.get_task(self.task.id), before)

    def test_measured_failed_draft_can_save_unchanged_durations_over_http(self):
        self.task.status = "failed"
        self.task.shots = [Shot("shot_1", 1, 0.8, "短句", "空镜", "旁白", "字幕"), Shot("shot_2", 2, 3.65, "塔", "全景", "旁白", "字幕")]
        self.storage.save_task(self.task)
        status, task = self.request("PATCH", f"/api/tasks/{self.task.id}", {"title": "恢复草稿", "shots": [
            {"id": "shot_1", "duration_sec": 0.8}, {"id": "shot_2", "duration_sec": 3.65},
        ]})
        self.assertEqual(status, 200, task)
        self.assertEqual(task["status"], "draft")
        self.assertEqual(task["shots"][0]["duration_sec"], 0.8)

    def test_task_list_reports_unknown_cost_execution_billing_and_observations(self):
        status, response = self.request("GET", "/api/tasks?limit=50")
        self.assertEqual(status, 200)
        metrics = response["items"][0]["metrics"]
        self.assertEqual(metrics["cost"]["amount_status"], "not_reported")
        self.assertIsNone(metrics["cost"]["total"])
        self.assertIsNone(metrics["execution"]["last_seconds"])
        self.assertEqual(metrics["billing"]["by_currency"], {})
        self.assertEqual(metrics["publication_observations"], [])

    def test_billing_records_are_explicitly_sourced_and_kept_separate(self):
        status, response = self.request("POST", f"/api/tasks/{self.task.id}/billing", {
            "amount": 1.25, "currency": "CNY", "source": "Invoice INV-1",
            "captured_at": "2026-11-21", "note": "Test record",
        })
        self.assertEqual(status, 201, response)
        self.assertEqual(response["by_currency"], {"CNY": 1.25})
        task = self.storage.get_task(self.task.id)
        self.assertEqual(task["billing_records"][0]["source"], "Invoice INV-1")
        self.assertEqual(task["trace"], None)

    def test_publication_observations_upsert_by_post_and_window(self):
        endpoint = f"/api/tasks/{self.task.id}/publication-observations"
        record = {
            "platform": "youtube", "post_url": "https://youtube.com/watch?v=abc",
            "published_at": "2026-11-20", "observed_at": "2026-11-21", "window_days": 1,
            "metrics": {"views": 0, "likes": 2}, "source_note": "Creator analytics screenshot",
        }
        self.assertEqual(self.request("POST", endpoint, record)[0], 201)
        update = {**record, "observed_at": "2026-11-22", "metrics": {"views": 50, "likes": 3}}
        self.assertEqual(self.request("POST", endpoint, update)[0], 201)
        stored = self.storage.get_task(self.task.id)["publication_observations"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["metrics"]["views"], 50)
        self.assertEqual(len(stored[0]["revisions"]), 1)

    def test_publication_observation_invalid_input_is_rejected(self):
        status, body = self.request("POST", f"/api/tasks/{self.task.id}/publication-observations", {
            "platform": "youtube", "post_url": "javascript:alert(1)",
            "published_at": "2026-11-20", "observed_at": "2026-11-21", "window_days": 7,
            "metrics": {"views": -4},
        })
        self.assertEqual(status, 400, body)
        self.assertFalse(self.storage.get_task(self.task.id).get("publication_observations"))

    def test_script_export_is_available_without_rendering(self):
        status, body = self.request("GET", f"/api/tasks/{self.task.id}/export?format=md")
        self.assertEqual(status, 200)
        self.assertIn("分镜".encode("utf-8"), body)
        self.assertEqual(self.request("GET", f"/api/tasks/{self.task.id}/export?format=bogus")[0], 400)
        self.assertEqual(self.request("GET", "/api/tasks/missing/export?format=md")[0], 404)

    def test_invalid_usage_is_client_error_and_media_stays_within_root(self):
        self.assertEqual(self.request("GET", "/api/library/assets?usage=invalid")[0], 400)
        marker = self.settings.data_dir / "private.txt"
        marker.write_text("private", encoding="utf-8")
        for path in ("/library-media/../private.txt", "/library-media/%2e%2e/private.txt", "/media/%2e%2e/private.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)


if __name__ == "__main__":
    unittest.main()
