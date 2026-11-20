"""走产品 VideoRenderer.render，但在真正执行 ffmpeg 前截住请求。

请求时长 30 时，滤镜必须把时长锁在 30，静图用连续裁切运镜，配乐要淡入淡出、
曲子短于成片时靠 -stream_loop 续上。单镜头不需要 xfade。默认不压低 BGM
（sidechaincompress 只在显式选"轻微/标准"时才出现）。不在 pytest 里做 30 秒渲染。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.renderer import VideoRenderer


class RenderRequestTests(unittest.TestCase):
    def _render_args(self, root: Path, duck: dict | None = None) -> str:
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

        audio = {
            "voice_local_path": str(voice),
            "music_local_path": str(music),
            "requested_duration": 30,
            "cues": [{"shot_id": "shot_1", "start": 0.0, "end": 4.0, "text": "静图镜头"}],
        }
        if duck is not None:
            audio["duck"] = duck

        with patch("backend.app.services.renderer.run_ffmpeg", side_effect=_capture):
            with patch(
                "backend.app.services.renderer._probe_stream_types",
                return_value={"video", "audio"},
            ):
                render = VideoRenderer(root / "media").render(
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
                    audio,
                    False,
                    False,
                    "720x1280",
                    25,
                    1.0,
                    0.18,
                )
        self.assertIn(render["mix_strategy"], {"music-constant", "music-ducked-sidechain"})
        return "\n".join(" ".join(args) for args in captured)

    def _assert_common(self, joined: str) -> None:
        self.assertIn("eval=frame", joined)
        self.assertNotIn("zoompan=", joined)
        self.assertNotIn("xfade", joined)
        self.assertIn("-frames:v", joined)
        self.assertIn("750", joined)
        self.assertTrue("duration=30.000" in joined)
        self.assertNotIn("30.05", joined)

    def test_requested_30s_is_locked_and_music_stays_constant(self):
        with tempfile.TemporaryDirectory() as directory:
            joined = self._render_args(Path(directory))

        self._assert_common(joined)
        # 默认不动 BGM 的音量，避免音乐忽大忽小
        self.assertNotIn("sidechaincompress=", joined)
        # 结尾硬切与短曲补静音两个毛病都要修掉
        self.assertIn("afade=t=out", joined)
        self.assertIn("afade=t=in", joined)
        self.assertIn("-stream_loop -1", joined)

    def test_music_ducking_can_be_turned_on_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            joined = self._render_args(Path(directory), duck={"mode": "标准"})

        self._assert_common(joined)
        self.assertIn("sidechaincompress=", joined)
        self.assertIn("afade=t=out", joined)


if __name__ == "__main__":
    unittest.main()
