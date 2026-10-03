"""六个镜头共用同一个风格前缀，种子稳定且彼此不同。不访问网络。"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
