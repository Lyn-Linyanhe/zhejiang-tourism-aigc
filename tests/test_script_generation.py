import json
import unittest

from backend.app.providers.llm import (
    OpenAICompatibleLLMProvider,
    _parse_json_object,
)

_VALIDATED_SHOTS = OpenAICompatibleLLMProvider._validated_shots


def _shot(**changes):
    shot = {
        "id": "shot_1",
        "order": 1,
        "duration_sec": 4,
        "title": "镜头一",
        "visual_prompt": "兰亭鹅池碑亭",
        "narration": "东晋永和九年，王羲之在兰亭写下兰亭集序。",
        "subtitle": "东晋永和九年 王羲之在兰亭写下兰亭集序",
        "transition": "fade",
    }
    shot.update(changes)
    return shot


def _result(shots):
    return {"title": "兰亭", "summary": "摘要", "cta": "来绍兴", "narration": "旁白", "shots": shots}


class JsonRepairTests(unittest.TestCase):
    def test_markdown_fence_is_stripped(self):
        result = _parse_json_object('```json\n{"title":"兰亭"}\n```')
        self.assertEqual(result["title"], "兰亭")

    def test_invalid_json_is_repaired_once(self):
        class StubProvider(OpenAICompatibleLLMProvider):
            def __init__(self):
                super().__init__("https://llm.invalid", "key", "model")
                self.contents = ["这不是 JSON", '{"title":"兰亭"}']
                self.calls = 0

            def _complete(self, messages):
                self.calls += 1
                return self.contents.pop(0)

        provider = StubProvider()
        self.assertEqual(provider._chat([])["title"], "兰亭")
        self.assertEqual(provider.calls, 2)

    def test_invalid_json_after_repair_raises(self):
        class StubProvider(OpenAICompatibleLLMProvider):
            def __init__(self):
                super().__init__("https://llm.invalid", "key", "model")

            def _complete(self, messages):
                return "还是不是 JSON"

        with self.assertRaisesRegex(RuntimeError, "after repair"):
            StubProvider()._chat([])


class ShotValidationTests(unittest.TestCase):
    def test_subtitle_must_match_narration_after_normalization(self):
        shots = _VALIDATED_SHOTS(_result([_shot()]), 1)
        self.assertEqual(shots[0]["subtitle"], "东晋永和九年 王羲之在兰亭写下兰亭集序")

    def test_subtitle_mismatch_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "subtitle does not match"):
            _VALIDATED_SHOTS(_result([_shot(subtitle="完全不同的字幕")]), 1)

    def test_unsupported_transition_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "unsupported transition"):
            _VALIDATED_SHOTS(_result([_shot(transition="explode")]), 1)

    def test_empty_shot_title_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "empty title"):
            _VALIDATED_SHOTS(_result([_shot(title="")]), 1)

    def test_wrong_shot_count_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "expected 2"):
            _VALIDATED_SHOTS(_result([_shot()]), 2)

    def test_validation_repair_retries_once(self):
        class StubProvider(OpenAICompatibleLLMProvider):
            def __init__(self):
                super().__init__("https://llm.invalid", "key", "model")
                self.results = [
                    _result([_shot(subtitle="错误字幕")]),
                    _result([_shot()]),
                ]
                self.calls = 0

            def _chat(self, messages):
                self.calls += 1
                return self.results.pop(0)

        provider = StubProvider()
        result, shots = provider._generate_validated([], 1)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(result["title"], "兰亭")
        self.assertEqual(shots[0]["transition"], "fade")


if __name__ == "__main__":
    unittest.main()
