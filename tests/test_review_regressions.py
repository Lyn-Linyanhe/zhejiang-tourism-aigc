"""Regressions for the publication and draft-edit paths found in PR review."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.config import Settings
from backend.app.models import Shot, Task, TaskInput, TraceRecord
from backend.app.providers.contract import AssetResult, AudioResult
from backend.app.providers.registry import _asset_dict_from_any
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = Settings(
            data_dir=self.root, audio_model_cache=self.root / "models", default_provider="offline",
            music_dir=self.root / "music", music_catalog_path=self.root / "music" / "catalog.json",
            allow_synthetic_audio_fallback=False,
        )
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.db_path)
        self.orchestrator = Orchestrator(self.settings, self.storage)
        self.task = Task(
            id="task_review", input=TaskInput.from_dict({
                "city": "杭州", "theme": "断桥残雪", "duration": 15,
                "voice": "无配音", "music_mood": "无音乐", "asset_source": "local-library",
            }), status="draft", stage="preview", title="标题", summary="摘要", cta="游杭州", narration="塔景",
            trace=TraceRecord("offline", "test", "v1", False),
        )
        self.task.shots = [Shot("shot_1", 1, 15, "雷峰塔", "雷峰塔全景", "塔景", "塔景")]
        self.storage.save_task(self.task)

    def asset(self):
        path = self.root / "tower.ppm"
        path.write_bytes(b"P6\n2 2\n255\n" + bytes(range(12)))
        return self.storage.register_library_asset({
            "local_path": str(path), "name": "tower", "asset_type": "image",
            "entity_id": "hz-leifengta", "city": "杭州", "shot_tags": ["wide"],
            "source_url": "https://example.org/tower", "source_title": "Tower",
            "author": "Photographer", "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "review_status": "approved", "license_status": "approved", "allowed_usages": ["preview", "publication"],
            "usage_scope": "Publication with attribution",
        })

    def test_music_failure_does_not_use_unapproved_directory_file(self):
        self.settings.music_dir.mkdir()
        (self.settings.music_dir / "unapproved.wav").write_bytes(b"not a licensed recording")
        self.settings.music_catalog_path.write_text(json.dumps({"tracks": [{
            "file": "unapproved.wav", "moods": ["舒缓"], "styles": ["诗意纪实"], "approved": False,
        }]}), encoding="utf-8")
        self.task.input.music_mood = "舒缓"
        result = AudioResult("", "", [], 15)
        with patch.object(self.orchestrator.audio_builder, "build", return_value=result), patch("backend.app.services.orchestrator.validate_audio_file"):
            with self.assertRaisesRegex(RuntimeError, "approved|authorized"):
                self.orchestrator._audio(self.task)
        self.assertFalse(self.task.audio.get("music_local_path"))

    def test_shot_entity_name_beats_task_theme(self):
        self.orchestrator._plan_shot(self.task, self.task.shots[0])
        self.assertEqual(self.task.shots[0].entity_id, "hz-leifengta")

    def test_unambiguous_shot_scenario_tag_resolves_entity(self):
        shot = self.task.shots[0]
        shot.title = shot.visual_prompt = "登高远景"
        shot.narration = ""
        self.orchestrator._plan_shot(self.task, shot)
        self.assertEqual(shot.entity_id, "hz-leifengta")

    def test_revoked_bound_preview_cannot_skip_asset_checks_on_rerender(self):
        row = self.asset()
        self.orchestrator.update_shot(self.task.id, "shot_1", {
            "entity_id": "hz-leifengta", "shot_tag": "wide", "library_asset_id": row["id"],
        })
        self.storage.update_library_asset(row["id"], {"review_status": "rejected"})
        with patch.object(self.orchestrator.registry, "create") as create:
            with self.assertRaisesRegex(RuntimeError, "preview|bound|usable"):
                self.orchestrator._render(self.orchestrator._load(self.task.id))
        create.assert_not_called()

    def test_publication_approval_is_invalidated_when_usage_scope_changes(self):
        row = self.asset()
        updated = self.storage.update_library_asset(row["id"], {"usage_scope": "仅内部评审，禁止发布"})
        self.assertEqual(updated["review_status"], "pending")
        self.assertEqual(updated["allowed_usages"], ["preview"])
        self.assertFalse(self.storage.list_library_assets(asset_id=row["id"], usage="publication"))

    def test_evidence_edits_do_not_reopen_a_rejected_asset(self):
        row = self.asset()
        self.storage.update_library_asset(row["id"], {"review_status": "rejected"})
        updated = self.storage.update_library_asset(row["id"], {"source_title": "Updated source title"})
        self.assertEqual(updated["review_status"], "rejected")
        self.assertEqual(updated["license_status"], "pending")
        self.assertEqual(updated["allowed_usages"], ["preview"])
        self.assertFalse(self.storage.list_library_assets(asset_id=row["id"], usage="publication"))

    def test_evidence_edit_can_explicitly_reject_again(self):
        row = self.asset()
        updated = self.storage.update_library_asset(row["id"], {
            "author": "Updated photographer", "review_status": "rejected",
        })
        self.assertEqual(updated["review_status"], "rejected")
        self.assertEqual(updated["license_status"], "pending")
        self.assertEqual(updated["allowed_usages"], ["preview"])

    def test_source_title_cannot_replace_a_missing_source_url_at_approval(self):
        row = self.asset()
        with self.assertRaisesRegex(ValueError, "evidence"):
            self.storage.update_library_asset(row["id"], {"source_url": "", "review_status": "approved"})

    def test_balanced_duration_edits_and_bindings_are_saved_atomically(self):
        row = self.asset()
        self.task.shots = [Shot(f"shot_{i}", i, 3.75, "雷峰塔", "雷峰塔全景", "塔景", "塔景") for i in range(1, 5)]
        self.storage.save_task(self.task)
        saved = self.orchestrator.update_draft(self.task.id, {"title": "新标题", "shots": [
            {"id": "shot_1", "duration_sec": 4.25, "entity_id": "hz-leifengta", "shot_tag": "wide", "library_asset_id": row["id"]},
            {"id": "shot_2", "duration_sec": 3.25},
        ]})
        self.assertEqual([shot["duration_sec"] for shot in saved["shots"]], [4.25, 3.25, 3.75, 3.75])
        self.assertEqual(saved["shots"][0]["library_asset_id"], row["id"])
        before = self.storage.get_task(self.task.id)
        with self.assertRaises(ValueError):
            self.orchestrator.update_draft(self.task.id, {"title": "不应保存", "shots": [{"id": "shot_1", "duration_sec": 8}]})
        self.assertEqual(self.storage.get_task(self.task.id), before)

    def test_target_duration_change_validates_existing_shot_total(self):
        before = self.storage.get_task(self.task.id)
        with self.assertRaisesRegex(ValueError, "add up"):
            self.orchestrator.update_draft(self.task.id, {"input": {"duration": 30}})
        self.assertEqual(self.storage.get_task(self.task.id), before)

    def test_unchanged_measured_durations_can_be_saved_on_failed_draft(self):
        self.task.status = "failed"
        self.task.shots = [Shot("shot_1", 1, 0.8, "短句", "空镜", "话", "话"), Shot("shot_2", 2, 3.65, "塔", "全景", "话", "话")]
        self.task.audio.update(cue_source="provider", duration_sec=4.45)
        self.storage.save_task(self.task)
        self.orchestrator.update_draft(self.task.id, {"shots": [{"id": "shot_1", "duration_sec": 0.8, "title": "修改短句"}, {"id": "shot_2", "duration_sec": 3.65}]})
        saved = self.orchestrator.update_shot(self.task.id, "shot_1", {"duration_sec": 0.8, "title": "再修改"})
        self.assertEqual(saved["shots"][0]["title"], "再修改")
        self.assertEqual(saved["shots"][0]["duration_sec"], 0.8)

    def test_legacy_positional_task_input_volumes_are_unchanged(self):
        legacy = TaskInput("杭州", "西湖", "游杭州", "", "", "", 15, "诗意纪实", "无配音", "无音乐",
                           True, "", 0, True, "offline", "720x1280", 25, 1.0, 0.8, 0.2)
        restored = TaskInput.from_dict(vars(legacy))
        self.assertEqual((restored.voice_volume, restored.music_volume), (0.8, 0.2))
        self.assertEqual(restored.voice_pitch, 0)

    def test_b896_positional_task_input_shape_remains_supported(self):
        merged_shape = TaskInput("杭州", "西湖", "游杭州", "", "", "", 15, "诗意纪实", "无配音", "无音乐",
                                 True, "", 0, True, "offline", "720x1280", 25, 1.0,
                                 -2.0, "warm", 0.7, "edge", 0.8, 0.2, "标准", "publication")
        self.assertEqual((merged_shape.voice_pitch, merged_shape.voice_style, merged_shape.voice_expressiveness,
                          merged_shape.voice_engine, merged_shape.voice_volume, merged_shape.music_volume,
                          merged_shape.music_ducking, merged_shape.visual_usage),
                         (-2.0, "warm", 0.7, "edge", 0.8, 0.2, "标准", "publication"))

    def test_ai_remote_receipt_survives_adapter_and_task_trace(self):
        output = AssetResult("video.mp4", "video", "cloud-t2v", "test-model", 8, "塔全景",
                             event={"type": "video", "mode": "t2v", "remote_task_id": "remote_123"})
        asset = _asset_dict_from_any(output, self.task.id, vars(self.task.shots[0]))
        self.assertEqual(asset.get("event", {}).get("remote_task_id"), "remote_123")
        self.orchestrator._commit_shot(self.task, self.task.shots[0], asset)
        self.assertTrue(any(event.get("remote_task_id") == "remote_123" for event in self.task.trace.events))
        self.assertEqual(self.task.assets[0].provenance["remote_task_id"], "remote_123")


if __name__ == "__main__":
    unittest.main()
