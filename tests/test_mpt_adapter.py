import unittest
from pathlib import Path

from backend.app.providers.moneyprinterturbo import build_storyboard


class MoneyPrinterTurboAdapterTests(unittest.TestCase):
    def test_build_storyboard_maps_script_and_terms(self):
        data = build_storyboard(
            script="清晨的西湖映着山色。沿着湖岸走进宋韵街巷。最后在城市灯火中收束。",
            terms=["West Lake", "Song culture", "Hangzhou night"],
            context={
                "city": "杭州",
                "landmark": "西湖",
                "theme": "春日城市漫游",
                "duration": 15,
                "style": "诗意纪实",
            },
        )
        self.assertEqual(len(data["shots"]), 4)
        self.assertEqual(data["shots"][0]["duration_sec"], 3.75)
        self.assertIn("West Lake", data["shots"][0]["visual_prompt"])
        self.assertEqual(data["narration"], "清晨的西湖映着山色。沿着湖岸走进宋韵街巷。最后在城市灯火中收束。")

    def test_missing_root_is_reported_without_importing_upstream(self):
        from backend.app.providers.moneyprinterturbo import MoneyPrinterTurboLLMProvider

        with self.assertRaises(RuntimeError):
            MoneyPrinterTurboLLMProvider(Path("does-not-exist")).generate("", {"city": "杭州", "theme": "测试"})


if __name__ == "__main__":
    unittest.main()
