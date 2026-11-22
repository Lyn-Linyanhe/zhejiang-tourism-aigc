"""进程重启后 running 任务的恢复（IMPROVEMENTS 已知限制：任务状态曾只在内存里）。"""

import tempfile
import unittest
from pathlib import Path

from backend.app.config import Settings
from backend.app.models import Shot, Task, TaskInput
from backend.app.services.orchestrator import Orchestrator
from backend.app.storage import Storage


class RestartRecoveryTests(unittest.TestCase):
    def _orchestrator(self, root: Path) -> Orchestrator:
        settings = Settings(data_dir=root)
        settings.ensure_dirs()
        storage = Storage(settings.db_path)
        return Orchestrator(settings, storage)

    def _task(self, task_id: str, status: str, stage: str) -> Task:
        task = Task(
            id=task_id,
            input=TaskInput.from_dict({"city": "杭州", "theme": "重启恢复", "duration": 10, "shot_count": 2}),
            status=status,
            stage=stage,
        )
        task.shots = [
            Shot("shot_1", 1, 3.0, "一", "画面一", "旁白一", "字幕一"),
            Shot("shot_2", 2, 3.0, "二", "画面二", "旁白二", "字幕二"),
        ]
        return task

    def test_running_task_is_failed_and_retryable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = self._orchestrator(Path(directory))
            stuck = self._task("task_stuck", "running", "assets")
            queued = self._task("task_queued", "queued", "queued")
            completed = self._task("task_done", "completed", "review")
            for task in (stuck, queued, completed):
                orchestrator.storage.save_task(task)

            recovered = orchestrator.recover_interrupted_tasks()

            self.assertIn("task_stuck", recovered)
            self.assertIn("task_queued", recovered)
            self.assertNotIn("task_done", recovered)
            saved = orchestrator.storage.get_task("task_stuck")
            self.assertEqual(saved["status"], "failed")
            self.assertIn("服务重启", saved["error"])
            self.assertIn("assets", saved["error"])
            self.assertTrue(any(e.get("type") == "interrupted" for e in saved["trace"]["events"]))
            # 恢复后 retry 不再被 "Task is already running" 挡住（start 仍需 provider，
            # 这里只验证状态检查解锁，不真正启动线程）
            task_obj = orchestrator._load("task_stuck")
            self.assertEqual(task_obj.status, "failed")

    def test_recovery_skips_normal_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            orchestrator = self._orchestrator(Path(directory))
            done = self._task("task_done", "completed", "review")
            failed = self._task("task_failed", "failed", "audio")
            for task in (done, failed):
                orchestrator.storage.save_task(task)
            self.assertEqual(orchestrator.recover_interrupted_tasks(), [])


if __name__ == "__main__":
    unittest.main()
