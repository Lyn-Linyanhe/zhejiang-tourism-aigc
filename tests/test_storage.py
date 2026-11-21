import tempfile
import unittest
from pathlib import Path

from backend.app.models import Task, TaskInput
from backend.app.storage import Storage


class StorageTests(unittest.TestCase):
    def test_save_and_list(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(Path(directory) / "app.sqlite3")
            task = Task(id="task_test", input=TaskInput.from_dict({"city": "杭州", "theme": "测试"}))
            storage.save_task(task)
            self.assertEqual(storage.get_task("task_test")["id"], "task_test")
            self.assertEqual(len(storage.list_tasks()), 1)

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
