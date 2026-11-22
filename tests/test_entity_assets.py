"""Entity-bound publication selection and render entry points."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.config import Settings
from backend.app.models import AssetRecord, Shot, Task, TaskInput, TraceRecord
from backend.app.services.library import save_library_upload
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage


class EntityAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(data_dir=self.root, audio_model_cache=self.root / "models", render_provider="ffmpeg")
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.db_path)
        self.orchestrator = Orchestrator(self.settings, self.storage)

    def test_library_upload_is_hashed_and_registered(self):
        content = b"uploaded image bytes"
        asset = save_library_upload(
            self.storage,
            self.settings.library_dir,
            "west-lake.jpg",
            content,
            {
                "entity_id": "hz-leifengta",
                "city": "杭州",
                "shot_tags": ["wide"],
                "source_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
                "author": "Photographer",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
            },
        )
        path = Path(asset["local_path"])
        self.assertTrue(path.is_file())
        self.assertEqual(asset["asset_type"], "image")
        self.assertEqual(asset["sha256"], __import__("hashlib").sha256(content).hexdigest())
        self.assertTrue(asset["url"].startswith("/library-media/"))
        self.assertEqual(self.storage.get_library_asset(asset["id"])["entity_id"], "hz-leifengta")

    def test_library_upload_rejects_unsupported_extension(self):
        with self.assertRaisesRegex(ValueError, "image and video"):
            save_library_upload(self.storage, self.settings.library_dir, "notes.txt", b"text", {})

    def _asset(self, name, **overrides):
        path = self.root / f"{name}.ppm"
        path.write_bytes(b"P6\n2 2\n255\n" + name.encode().ljust(12, b"z")[:12])
        return self.storage.register_library_asset({
            "local_path": str(path), "name": name, "asset_type": "image",
            "entity_id": "hz-leifengta", "city": "杭州", "shot_tags": ["wide"],
            "source_url": "https://example.org/original", "author": "Test photographer",
            "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "review_status": "approved", "license_status": "approved",
            "allowed_usages": ["preview", "publication"], **overrides,
        })

    def _task(self, **overrides):
        task = Task(id="task_publication", input=TaskInput.from_dict({
            "city": "杭州市", "theme": "西湖春游", "duration": 15, "visual_usage": "publication",
            "asset_source": "offline", "voice": "无配音", "music_mood": "无音乐",
            "include_subtitles": False, "include_ai_label": False, **overrides,
        }), status="draft", stage="preview", trace=TraceRecord("offline", "test", "v1", False))
        task.shots = [Shot("shot_1", 1, 15, "雷峰塔全景", "雷峰塔与远山全景", "雷峰塔立于湖畔。", "雷峰塔立于湖畔。")]
        self.storage.save_task(task)
        return task

    def test_publication_lookup_requires_exact_intent_and_valid_review(self):
        correct = self._asset("correct")
        invalid = {
            "pending": {"review_status": "pending"},
            "rejected": {"review_status": "rejected"},
            "rights": {"license_status": "pending"},
            "private": {"allowed_usages": ["preview"]},
            "expired": {"valid_until": "2025-12-31"},
            "other-city": {"entity_id": "nb-intangible-ninghai-pingdiao", "city": "宁波"},
            "detail": {"shot_tags": ["detail"]},
            "no-proof": {"license_url": "", "license_evidence": ""},
        }
        for name, fields in invalid.items():
            with self.subTest(name=name):
                self._asset(name, **fields)
        filters = {"usage": "publication", "now": "2026-01-01", "entity_id": "hz-leifengta", "shot_tag": "wide"}
        self.assertEqual(self.storage.find_library_asset("image", **filters)["id"], correct["id"])
        self.storage.update_library_asset(correct["id"], {"review_status": "rejected"})
        self.assertIsNone(self.storage.find_library_asset("image", **filters))

    def test_rescanning_same_file_preserves_review(self):
        row = self._asset("reviewed")
        self.storage.scan_library(self.root)
        found = self.storage.find_library_asset("image", entity_id="hz-leifengta", shot_tag="wide", usage="publication")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], row["id"])

    def test_library_review_patch_rejects_unknown_or_malformed_values(self):
        row = self._asset("review-patch")
        for patch in (
            {"review_status": "maybe"},
            {"license_status": "unchecked"},
            {"allowed_usages": ["publication", "admin"]},
            {"shot_tags": ["unstructured"]},
            {"valid_until": "2026-02-31"},
            {"sha256": "forged"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.storage.update_library_asset(row["id"], patch)

    def test_publication_selection_skips_assets_with_changed_fingerprints(self):
        changed = self._asset("changed-first")
        selected = self._asset("unchanged-second")
        Path(changed["local_path"]).write_bytes(b"file content changed")
        result = self.storage.find_library_asset(
            "image", entity_id="hz-leifengta", shot_tag="wide", usage="publication",
        )
        self.assertEqual(result["id"], selected["id"])

    def test_shot_entity_tag_and_library_binding_are_persisted(self):
        row = self._asset("bound")
        task = self._task()
        result = self.orchestrator.update_shot(task.id, "shot_1", {
            "entity_id": "hz-leifengta", "shot_tag": "wide", "library_asset_id": row["id"],
        })
        shot = result["shots"][0]
        self.assertEqual(shot["entity_id"], "hz-leifengta")
        self.assertEqual(shot["shot_tag"], "wide")
        self.assertEqual(shot["library_asset_id"], row["id"])
        self.assertEqual(shot["asset_local_path"], row["local_path"])

    def test_shot_binding_rejects_asset_for_a_different_entity(self):
        row = self._asset("foreign", entity_id="nb-intangible-ninghai-pingdiao", city="宁波")
        task = self._task()
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.orchestrator.update_shot(task.id, "shot_1", {
                "entity_id": "hz-leifengta", "shot_tag": "wide", "library_asset_id": row["id"],
            })

    def test_publication_resolves_shot_and_selects_library_before_preview_sources(self):
        row = self._asset("approved")
        self._asset("pending", review_status="pending")
        task = self._task()
        self.orchestrator._assets(task)
        self.assertEqual((task.shots[0].entity_id, task.shots[0].shot_tag), ("hz-leifengta", "wide"))
        self.assertEqual(task.assets[0].local_path, row["local_path"])
        self.assertEqual(task.assets[0].provenance["library_asset_id"], row["id"])
        self.assertEqual(task.assets[0].provenance["source_url"], row["source_url"])
        self.assertEqual(task.assets[0].provenance["sha256"], row["sha256"])
        self.assertTrue(any(e.get("provenance", {}).get("library_asset_id") == row["id"] for e in task.trace.events))

    def test_publication_does_not_fall_back_when_bound_asset_is_pending(self):
        self._asset("approved")
        pending = self._asset("pending", review_status="pending")
        task = self._task()
        task.shots[0].library_asset_id = pending["id"]
        with self.assertRaisesRegex(RuntimeError, "No approved publication asset"):
            self.orchestrator._assets(task)
        self.assertFalse(task.assets)

    def test_publication_render_retry_rejects_an_unreviewed_upload(self):
        task = self._task()
        raw = self._asset("unreviewed", review_status="pending")
        self.storage.append_asset(task.id, vars(AssetRecord(
            "upload_test", task.id, "shot_1", "upload", "", "local-upload", "pending", "", "", False,
            local_path=raw["local_path"],
        )))
        with patch("backend.app.services.renderer.detect_focus") as focus:
            self.orchestrator._run(task.id, "rendering")
        focus.assert_not_called()
        saved = self.storage.get_task(task.id)
        self.assertEqual(saved["status"], "failed")
        self.assertIn("No approved publication asset", saved["error"])
        self.assertFalse(saved["render"])

    def test_publication_render_retry_rechecks_revoked_and_changed_files(self):
        row = self._asset("approved")
        task = self._task()
        self.orchestrator._assets(task)
        for reason in ("revoked", "changed"):
            with self.subTest(reason=reason), patch("backend.app.services.renderer.detect_focus") as focus:
                if reason == "revoked":
                    self.storage.update_library_asset(row["id"], {"review_status": "rejected"})
                else:
                    self.storage.update_library_asset(row["id"], {"review_status": "approved"})
                    Path(row["local_path"]).write_bytes(b"different content")
                self.orchestrator._run(task.id, "rendering")
                focus.assert_not_called()
                saved = self.storage.get_task(task.id)
                self.assertEqual(saved["status"], "failed")
                self.assertIn("publication asset", saved["error"])

    def test_publication_review_rejects_blank_license_evidence(self):
        row = self._asset("blank-evidence", license_url="", license_evidence="   ", review_status="pending")
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.storage.update_library_asset(row["id"], {
                "review_status": "approved", "license_status": "approved", "allowed_usages": ["publication"],
            })
        self.assertEqual(self.storage.list_library_assets(usage="publication"), [])

    def test_publication_render_retry_rechecks_task_city(self):
        self._asset("approved-city")
        task = self._task()
        self.orchestrator._assets(task)
        self.orchestrator.update_draft(task.id, {"input": {"city": "宁波"}})
        with patch("backend.app.services.renderer.detect_focus") as focus:
            self.orchestrator._run(task.id, "rendering")
        focus.assert_not_called()
        saved = self.storage.get_task(task.id)
        self.assertEqual(saved["status"], "failed")
        self.assertIn("city", saved["error"])

    def test_entity_preview_does_not_choose_another_city(self):
        self._asset("foreign-preview", entity_id="nb-intangible-ninghai-pingdiao", city="宁波")
        provider = self.orchestrator.registry.create("asset", "local-library")
        with self.assertRaisesRegex(RuntimeError, "no usable"):
            provider.create_or_find("preview", {
                "id": "shot", "title": "雷峰塔", "entity_id": "hz-leifengta", "shot_tag": "wide",
            }, self.root, "#123456", "#abcdef", {"city": "杭州", "visual_usage": "preview"})

    def test_publication_render_checks_task_record_file(self):
        row = self._asset("approved-record")
        wrong = self._asset("wrong-record")
        task = self._task()
        task.shots[0].library_asset_id = row["id"]
        self.orchestrator._assets(task)
        task.assets[0].local_path = wrong["local_path"]
        self.storage.save_task(task)
        with patch("backend.app.services.renderer.detect_focus") as focus:
            self.orchestrator._run(task.id, "rendering")
        focus.assert_not_called()
        self.assertEqual(self.storage.get_task(task.id)["status"], "failed")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
    def test_publication_manifest_records_the_selected_asset(self):
        row = self._asset("approved")
        task = self._task()
        task.shots[0].focus_x = task.shots[0].focus_y = 0.5
        self.orchestrator._assets(task)
        self.orchestrator._run(task.id, "rendering")
        saved = self.storage.get_task(task.id)
        self.assertEqual(saved["status"], "completed", saved["error"])
        manifest = json.loads((self.settings.media_dir / task.id / "manifest.json").read_text(encoding="utf-8"))
        shot = manifest["shots"][0]
        self.assertEqual(shot["entity_id"], "hz-leifengta")
        self.assertEqual(shot["shot_tag"], "wide")
        self.assertEqual(shot["library_asset_id"], row["id"])
        self.assertEqual(shot["provenance"]["license_status"], "approved")
        self.assertEqual(shot["provenance"]["source_url"], row["source_url"])
        self.assertIn("width", shot["provenance"])
        self.assertIn("height", shot["provenance"])
        self.assertTrue(shot["provenance"]["search_query"])
