import json
import unittest

from backend.app.services.script_export import export_script


TASK = {
    "id": "task_export_1",
    "title": "兰亭一日",
    "summary": "从鹅池走到曲水流觞。",
    "cta": "来绍兴，赴一场兰亭雅集。",
    "narration": "兰亭在绍兴城西南。东晋永和九年，王羲之在此写下兰亭集序。",
    "tags": ["绍兴", "书法"],
    "input": {
        "city": "绍兴",
        "landmark": "兰亭",
        "theme": "书法与曲水流觞",
        "style": "诗意纪实",
        "duration": 30,
        "output_resolution": "720x1280",
        "fps": 25,
        "voice": "女声",
        "music_mood": "无音乐",
        "visual_usage": "preview",
    },
    "trace": {"provider": "openai-compatible", "model": "deepseek-chat", "fallback_used": False},
    "shots": [
        {
            "id": "shot_1",
            "order": 1,
            "duration_sec": 4.25,
            "title": "鹅池",
            "visual_prompt": "鹅池碑亭与竹林",
            "narration": "兰亭在绍兴城西南。",
            "subtitle": "兰亭在绍兴城西南",
            "transition": "fade",
            "entity_id": "sx-lanting",
            "shot_tag": "detail",
        }
    ],
    "created_at": "2026-11-21T10:00:00+08:00",
    "updated_at": "2026-11-21T10:05:00+08:00",
}


class ScriptExportTests(unittest.TestCase):
    def test_markdown_contains_script_metadata_and_storyboard(self):
        body, content_type, filename = export_script(TASK, "md")
        text = body.decode("utf-8")
        self.assertEqual(content_type, "text/markdown; charset=utf-8")
        self.assertEqual(filename, "script_task_export_1.md")
        for value in ("兰亭一日", "全片旁白", "分镜", "鹅池", "鹅池碑亭与竹林", "deepseek-chat"):
            self.assertIn(value, text)

    def test_plain_text_export_is_readable(self):
        body, content_type, filename = export_script(TASK, "txt")
        text = body.decode("utf-8")
        self.assertEqual(content_type, "text/plain; charset=utf-8")
        self.assertEqual(filename, "script_task_export_1.txt")
        self.assertIn("标题：兰亭一日", text)
        self.assertIn("[镜头 1] 4.25 秒", text)

    def test_json_export_is_structured(self):
        body, content_type, filename = export_script(TASK, "json")
        payload = json.loads(body)
        self.assertEqual(content_type, "application/json; charset=utf-8")
        self.assertEqual(filename, "script_task_export_1.json")
        self.assertEqual(payload["metadata"]["city"], "绍兴")
        self.assertEqual(payload["metadata"]["provider"], "openai-compatible")
        self.assertEqual(payload["shots"][0]["entity_id"], "sx-lanting")

    def test_table_cells_escape_pipes_and_newlines(self):
        task = {**TASK, "shots": [{**TASK["shots"][0], "visual_prompt": "左边|右边\n第二行"}]}
        text = export_script(task, "md")[0].decode("utf-8")
        self.assertIn("左边\\|右边<br>第二行", text)

    def test_unknown_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "format must be"):
            export_script(TASK, "docx")

    def test_empty_task_still_exports(self):
        body, _, _ = export_script({"id": "empty"}, "md")
        text = body.decode("utf-8")
        self.assertIn("未命名脚本", text)
        self.assertIn("（无分镜）", text)


if __name__ == "__main__":
    unittest.main()
