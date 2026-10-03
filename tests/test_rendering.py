import tempfile
import unittest
from pathlib import Path

from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.renderer import VideoRenderer


class RenderingTests(unittest.TestCase):
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
