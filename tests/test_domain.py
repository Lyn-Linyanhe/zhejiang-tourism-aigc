import unittest

from backend.app.config import PROJECT_ROOT
from backend.app.models import TaskInput
from backend.app.providers.offline import OfflineLLMProvider


class DomainTests(unittest.TestCase):
    def test_task_input_validation(self):
        value = TaskInput.from_dict(
            {"city": "杭州", "theme": "春日城市漫游", "duration": 30}
        )
        self.assertEqual(value.city, "杭州")
        self.assertEqual(value.output_resolution, "720x1280")
        self.assertEqual(value.asset_source, "configured")
        self.assertEqual(value.shot_count, 0)
        with self.assertRaises(ValueError):
            TaskInput.from_dict({"city": "", "theme": "测试"})

    def test_custom_render_settings_are_validated(self):
        value = TaskInput.from_dict(
            {
                "city": "杭州",
                "theme": "测试",
                "duration": 15,
                "shot_count": 4,
                "asset_source": "upload",
                "output_resolution": "1080x1920",
                "fps": 30,
                "voice_rate": 1.2,
                "voice_volume": 0.8,
                "music_volume": 0.2,
            }
        )
        self.assertEqual(value.output_resolution, "1080x1920")
        self.assertEqual(value.fps, 30)
        with self.assertRaises(ValueError):
            TaskInput.from_dict(
                {"city": "杭州", "theme": "测试", "duration": 15, "fps": 23}
            )

    def test_offline_provider_returns_structured_storyboard(self):
        result = OfflineLLMProvider().generate(
            "",
            {"city": "绍兴", "theme": "黄酒文化", "duration": 30, "style": "国风雅韵"},
        )
        self.assertEqual(result.provider, "offline-zhejiang-editor")
        self.assertEqual(len(result.data["shots"]), 6)
        self.assertTrue(result.data["narration"])
        self.assertTrue(all(item["subtitle"] for item in result.data["shots"]))

    def test_project_root_is_stable(self):
        self.assertTrue((PROJECT_ROOT / "backend").exists())


if __name__ == "__main__":
    unittest.main()
