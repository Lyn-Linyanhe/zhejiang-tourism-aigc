import tempfile
import unittest
import json
import subprocess
from pathlib import Path

from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.renderer import VideoRenderer


class RenderingTests(unittest.TestCase):
    def test_two_images_have_real_transition_and_preserve_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            red = root / "red.ppm"
            blue = root / "blue.ppm"
            red.write_bytes(b"P6\n16 9\n255\n" + bytes((220, 30, 30)) * 16 * 9)
            blue.write_bytes(b"P6\n16 9\n255\n" + bytes((30, 30, 220)) * 16 * 9)
            VideoRenderer(root / "media").render(
                "focus_transition",
                [
                    {"id": "shot_1", "duration_sec": 2, "asset_local_path": str(red),
                     "asset_media_type": "image", "motion": "push", "focus_x": 0.8,
                     "focus_y": 0.4, "subtitle": "红色"},
                    {"id": "shot_2", "duration_sec": 2, "asset_local_path": str(blue),
                     "asset_media_type": "image", "transition": "dissolve",
                     "motion": "pull", "subtitle": "蓝色"},
                ],
                {"requested_duration": 4}, False, False, "320x180", 25,
            )
            output = root / "media" / "focus_transition" / "final.mp4"
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
                capture_output=True, text=True, check=True,
            )
            self.assertAlmostEqual(float(probe.stdout.strip()), 4.0, places=2)
            manifest = json.loads((output.parent / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["focus_points"][0]["method"], "manual")
            self.assertEqual(manifest["focus_points"][0]["x"], 0.8)
            self.assertEqual(manifest["transitions"][0]["style"], "dissolve")
            # At the nominal 2 s boundary, both source pictures must appear.
            middle = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "2",
                 "-i", str(output), "-frames:v", "1", "-f", "rawvideo",
                 "-pix_fmt", "rgb24", "-"],
                capture_output=True, check=True,
            ).stdout
            pixel = middle[(90 * 320 + 160) * 3:(90 * 320 + 160) * 3 + 3]
            self.assertGreater(pixel[0], 60)
            self.assertGreater(pixel[2], 60)

    def test_renderer_outputs_video_audio_and_subtitles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "scene.ppm"
            asset.write_bytes(
                b"P6\n2 2\n255\n"
                + bytes((20, 90, 120, 20, 90, 120, 230, 180, 80, 230, 180, 80))
            )
            voice = root / "voice.wav"
            generate_tone(voice, 2.0, 440, 0.12)
            render = VideoRenderer(root / "media").render(
                "task_test",
                [
                    {
                        "id": "shot_1",
                        "duration_sec": 2,
                        "subtitle": "测试字幕",
                        "asset_local_path": str(asset),
                    }
                ],
                {"voice_local_path": str(voice)},
                True,
                True,
                "720x1280",
                25,
                1.0,
                0.0,
            )
            self.assertTrue(render["has_audio"])
            self.assertTrue(render["has_burned_subtitles"])
            self.assertEqual(render["resolution"], "720x1280")
            self.assertTrue((root / "media" / "task_test" / "final.mp4").exists())
            self.assertTrue((root / "media" / "task_test" / "subtitles.srt").exists())
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=channels", "-of", "json",
                 str(root / "media" / "task_test" / "final.mp4")],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(json.loads(probe.stdout)["streams"][0]["channels"], 2)
            ass = (root / "media" / "task_test" / "subtitles.ass").read_text(encoding="utf-8-sig")
            self.assertIn("PlayResX: 720", ass)
            self.assertIn("PlayResY: 1280", ass)
            self.assertIn("测试字幕", ass)

    def test_renderer_can_render_without_audio_when_user_disables_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "scene.ppm"
            asset.write_bytes(
                b"P6\n2 2\n255\n"
                + bytes((20, 90, 120, 20, 90, 120, 230, 180, 80, 230, 180, 80))
            )
            render = VideoRenderer(root / "media").render(
                "task_silent",
                [
                    {
                        "id": "shot_1",
                        "duration_sec": 2,
                        "subtitle": "字幕可关闭",
                        "asset_local_path": str(asset),
                    }
                ],
                {},
                False,
                False,
            )
            self.assertFalse(render["has_audio"])
            self.assertFalse(render["has_burned_subtitles"])


if __name__ == "__main__":
    unittest.main()
