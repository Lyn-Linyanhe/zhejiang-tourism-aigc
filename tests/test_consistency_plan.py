"""六个镜头共用同一个风格前缀，种子稳定且彼此不同。不访问网络。"""

import tempfile
import unittest
from pathlib import Path

from backend.app.services import consistency


class ConsistencyPlanTests(unittest.TestCase):
    def test_six_shots_share_prefix_and_distinct_stable_seeds(self):
        shots = [
            {"id": f"shot_{order}", "order": order, "visual_prompt": f"西湖镜头{order}"}
            for order in range(1, 7)
        ]
        style_ctx = {"style": "雅致", "color": "#24526a", "accent": "#d6ad60"}

        first = consistency.plan("task_westlake", shots, style_ctx)
        second = consistency.plan("task_westlake", shots, style_ctx)

        planned = first["shots"]
        self.assertEqual(len(planned), 6)
        prefix = first["style_prefix"]
        self.assertTrue(prefix)
        self.assertEqual(prefix, second["style_prefix"])
        seeds = [item["seed"] for item in planned]
        self.assertEqual(len(set(seeds)), 6)
        self.assertEqual(seeds, [item["seed"] for item in second["shots"]])
        for item in planned:
            self.assertTrue(item["prompt"].startswith(prefix))
            self.assertEqual(item["style_prefix"], prefix)
            self.assertEqual(item["negative_prompt"], first["negative_prompt"])
        self.assertEqual(first["seed_base"], second["seed_base"])

    def test_subject_lock_and_reference_are_machine_verifiable(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "subject.png"
            reference.write_bytes(b"same-subject-reference")
            shots = [
                {"id": f"shot_{order}", "order": order, "visual_prompt": f"断桥第{order}个机位"}
                for order in range(1, 5)
            ]
            plan = consistency.plan(
                "task_lock",
                shots,
                {
                    "city": "杭州",
                    "landmark": "西湖",
                    "subject_lock": "同一座断桥，石栏缺口和青灰石材保持一致",
                    "subject_reference_path": str(reference),
                },
            )
            result = consistency.validate_plan(plan)
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(result["anchored_shot_count"], 4)
            lock = plan["subject_lock"]
            self.assertTrue(lock["id"].startswith("subject_"))
            self.assertEqual(len(lock["reference_sha256"]), 64)
            for index, item in enumerate(plan["shots"]):
                self.assertIn(lock["id"], item["prompt"])
                self.assertIn("同一座断桥", item["prompt"])
                self.assertIn(f"断桥第{index + 1}个机位", item["prompt"])
                self.assertEqual(item["reference_sha256"], lock["reference_sha256"])

            plan["shots"][2]["reference_sha256"] = "tampered"
            self.assertFalse(consistency.validate_plan(plan)["passed"])


if __name__ == "__main__":
    unittest.main()
