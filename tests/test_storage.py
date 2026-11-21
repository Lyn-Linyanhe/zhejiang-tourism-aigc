import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from backend.app.config import settings
from backend.app.models import Task, TaskInput
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage, TaskBusyError


class StorageTests(unittest.TestCase):
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "app.sqlite3")
            task = Task(id="task_test", input=TaskInput.from_dict({"city": "杭州", "theme": "测试"}))
            storage.save_task(task)
            self.assertEqual(storage.get_task("task_test")["id"], "task_test")
            self.assertEqual(len(storage.list_tasks()), 1)

    def test_preview_preserves_first_submission_after_draft_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(settings, data_dir=Path(directory), default_provider="offline")
            storage = Storage(config.db_path)
            orchestrator = Orchestrator(config, storage)
            initial = TaskInput.from_dict({
                "city": "杭州", "theme": "西湖秋游", "asset_source": "pexels",
                "duration": 15, "fps": 24, "include_subtitles": False,
            })

            task = orchestrator.create_preview(initial)
            self.assertEqual(task.status, "draft")
            orchestrator.update_draft(task.id, {"input": {"fps": 30, "include_subtitles": True}})
            saved = storage.get_task(task.id)

            self.assertEqual(saved["initial_input"], vars(initial))
            self.assertEqual(saved["input"]["fps"], 30)
            self.assertTrue(saved["input"]["include_subtitles"])
            self.assertEqual(saved["input"]["asset_source"], "pexels")

    def test_append_asset_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "app.sqlite3")
            task = Task(id="task_asset", input=TaskInput.from_dict({"city": "杭州", "theme": "素材测试"}))
            storage.save_task(task)
            asset = {
                "id": "upload_1",
                "task_id": "task_asset",
                "shot_id": "",
                "name": "licensed.jpg",
                "url": "/media/task_asset/licensed.jpg",
                "source": "local-upload",
                "license": "team-owned",
                "author": "team",
                "usage_scope": "competition",
                "is_ai_generated": False,
                "local_path": str(Path(directory) / "licensed.jpg"),
            }
            storage.append_asset("task_asset", asset)
            saved = storage.get_task("task_asset")
            self.assertEqual(saved["assets"][0]["license"], "team-owned")

    def test_trash_migrates_existing_data_and_preserves_task_on_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "app.sqlite3"
            storage = Storage(db_path)
            task = Task(
                id="historic_task", status="completed", stage="review",
                input=TaskInput.from_dict({"city": "杭州", "theme": "西湖"}),
            )
            storage.save_task(task)
            storage.append_asset(task.id, {"id": "historic_asset", "created_at": task.updated_at})
            original = storage.get_task(task.id)
            with closing(sqlite3.connect(db_path)) as connection, connection:
                connection.execute("ALTER TABLE tasks DROP COLUMN trashed_at")

            storage = Storage(db_path)
            deleted_at = storage.trash_task(task.id)
            self.assertEqual(storage.trash_task(task.id), deleted_at)
            reopened = Storage(db_path)
            self.assertIsNone(reopened.get_task(task.id))
            self.assertFalse(reopened.list_tasks())
            self.assertEqual(reopened.list_trashed_tasks(), [dict(original, trashed_at=deleted_at)])
            with self.assertRaises(KeyError):
                reopened.save_task(task)
            with closing(sqlite3.connect(db_path)) as connection, connection:
                self.assertEqual(connection.execute("SELECT id FROM assets").fetchone(), ("historic_asset",))

            reopened.restore_task(task.id)
            self.assertEqual(reopened.get_task(task.id), original)
            self.assertFalse(reopened.list_trashed_tasks())

    def test_running_task_cannot_move_to_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "app.sqlite3")
            task = Task(
                id="running_task", status="running", stage="rendering",
                input=TaskInput.from_dict({"city": "杭州", "theme": "西湖"}),
            )
            storage.save_task(task)
            with self.assertRaises(TaskBusyError):
                storage.trash_task(task.id)
            self.assertEqual(storage.list_tasks()[0]["id"], task.id)
            self.assertFalse(storage.list_trashed_tasks())

    def test_library_asset_is_deduplicated_and_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            storage = Storage(root / "app.sqlite3")
            asset_path = root / "scene.ppm"
            asset_path.write_bytes(b"P6\\n1 1\\n255\\n" + bytes((1, 2, 3)))
            first = storage.register_library_asset({"local_path": str(asset_path), "name": "scene", "asset_type": "image"})
            second = storage.register_library_asset({"local_path": str(asset_path), "name": "scene", "asset_type": "image"})
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(storage.list_library_assets("image", "scene")), 1)


if __name__ == "__main__":
    unittest.main()
