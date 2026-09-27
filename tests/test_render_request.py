"""走产品 VideoRenderer.render，但在真正执行 ffmpeg 前截住请求。

请求时长 30 时，滤镜必须把时长锁在 30，静图用连续裁切运镜，有配乐含
sidechaincompress，且不含 xfade。不在 pytest 里做 30 秒渲染。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.renderer import VideoRenderer


class RenderRequestTests(unittest.TestCase):
    def test_requested_30s_is_locked_without_running_a_long_render(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "scene.ppm"
            asset.write_bytes(b"P6\n2 2\n255\n" + bytes([20, 90, 120]) * 4)
            voice = root / "voice.wav"
            music = root / "music.wav"
            generate_tone(voice, 0.3, 440, 0.12)
            generate_tone(music, 0.3, 220, 0.08)
            captured: list[list[str]] = []

            def _capture(args, cwd=None):
                captured.append(list(args))

                class _Done:
                    returncode = 0
                    stderr = ""
                    stdout = ""

                return _Done()

            with patch("backend.app.services.renderer.run_ffmpeg", side_effect=_capture):
                with patch("backend.app.services.renderer._probe_stream_types", return_value={"video", "audio"}):
                    VideoRenderer(root / "media").render(
                        "task_30",
                        [
                            {
                                "id": "shot_1",
                                "duration_sec": 30,
                                "subtitle": "静图镜头",
                                "asset_local_path": str(asset),
                                "asset_media_type": "image",
                                "transition": "fade",
                            }
                        ],
                        {
                            "voice_local_path": str(voice),
                            "music_local_path": str(music),
                            "requested_duration": 30,
                            "cues": [
                                {"shot_id": "shot_1", "start": 0.0, "end": 4.0, "text": "静图镜头"}
                            ],
                        },
                        False,
                        False,
                        "720x1280",
                        25,
                        1.0,
                        0.18,
                    )

        joined = "\n".join(" ".join(args) for args in captured)
        self.assertIn("eval=frame", joined)
        self.assertNotIn("zoompan=", joined)
        self.assertIn("sidechaincompress=", joined)
        self.assertNotIn("xfade", joined)
        self.assertIn("-frames:v", joined)
        self.assertIn("750", joined)
        self.assertTrue(any("duration=30.000" in " ".join(args) for args in captured))
        self.assertNotIn("30.05", joined)


if __name__ == "__main__":
    unittest.main()
