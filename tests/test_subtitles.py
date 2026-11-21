import re
import tempfile
import unittest
from pathlib import Path

from backend.app.providers.subtitles import caption_style, create_ass, layout_caption


class CaptionTests(unittest.TestCase):
    def test_style_scales_with_canvas_and_leaves_a_safe_area(self):
        portrait = caption_style(720, 1280)
        hd = caption_style(1080, 1920)
        landscape = caption_style(1920, 1080)
        self.assertEqual(portrait.font_size, 44)
        self.assertEqual(hd.font_size, 66)
        self.assertGreaterEqual(portrait.margin_bottom, 150)
        self.assertEqual(landscape.font_size, 66)
        self.assertLess(landscape.margin_bottom / 1080, portrait.margin_bottom / 1280)

    def test_short_caption_remains_one_line(self):
        self.assertEqual(layout_caption("雨中的断桥，行人撑伞走过。", caption_style(720, 1280)), (["雨中的断桥，行人撑伞走过。"], 44))

    def test_long_sentence_wraps_at_a_phrase_and_keeps_the_year(self):
        text = "西湖龙井制茶技艺，是2022年入选的中国传统制茶非遗项目的一部分。"
        lines, size = layout_caption(text, caption_style(720, 1280))
        self.assertEqual(len(lines), 2)
        self.assertEqual("".join(lines), text)
        self.assertTrue(any("2022年" in line for line in lines))
        self.assertFalse(any(line.startswith(tuple("，。！？；：、")) for line in lines))
        self.assertGreaterEqual(size, 24)

    def test_small_canvas_retains_words_and_parentheses(self):
        text = "在杭州（Hangzhou），公元326年的记忆仍然清晰。"
        lines, _ = layout_caption(text, caption_style(320, 568))
        self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
        self.assertTrue(any("Hangzhou" in line for line in lines))
        self.assertTrue(any("公元326年" in line for line in lines))
        self.assertFalse(any(line.endswith("（") or line.startswith("）") for line in lines))

    def test_explicit_line_breaks_and_spaces_are_preserved(self):
        lines, _ = layout_caption("西湖  夜游\n灵隐寺\n龙井", caption_style(720, 1280))
        self.assertEqual(lines, ["西湖  夜游", "灵隐寺", "龙井"])

    def test_oversized_english_token_can_wrap_without_losing_characters(self):
        text = "W" * 50
        lines, size = layout_caption(text, caption_style(320, 568))
        self.assertEqual("".join(lines), text)
        self.assertGreater(len(lines), 1)
        self.assertGreaterEqual(size, 10)

    def test_plain_path_and_braces_remain_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:02,000\nC:\\WestLake\\Day1 {原样}\n", encoding="utf-8")
            create_ass(path, root / "out.ass", 720, 1280)
            output = (root / "out.ass").read_text(encoding="utf-8-sig").replace("\u2060", "")
            self.assertIn("C:\\WestLake\\Day1", output)
            self.assertIn(r"\{原样\}", output)
            self.assertNotIn("＼", output)

    def test_ass_keeps_original_srt_and_cue_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "subtitles.srt"
            original = "1\n00:00:01,120 --> 00:00:04,350\n公元326年，灵隐寺在杭州始建。\n\n"
            path.write_text(original, encoding="utf-8")
            info = create_ass(path, root / "subtitles.ass", 720, 1280)
            result = (root / "subtitles.ass").read_text(encoding="utf-8-sig")
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertIn("PlayResX: 720", result)
            self.assertIn("PlayResY: 1280", result)
            self.assertIn("0:00:01.12,0:00:04.35", result)
            self.assertIn("Style: Caption,", result)
            self.assertEqual(info["cue_count"], 1)

    def test_source_text_cannot_inject_ass_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.srt"
            path.write_text("1\n00:00:00,000 --> 00:00:02,000\n{\\pos(0,0)}\\N西湖\n", encoding="utf-8")
            create_ass(path, root / "out.ass", 720, 1280)
            output = (root / "out.ass").read_text(encoding="utf-8-sig")
            self.assertNotIn(r"{\pos(0,0)}", output)
            self.assertIn("西湖", output)


if __name__ == "__main__":
    unittest.main()
