"""混音响度的回归。

背景：amix 默认 normalize=1，会把每条输入都除以输入数——带 BGM 时人声被平白
压低 6dB，纯人声时又不会。结果就是成片响度全看有没有音乐（实测 -25.8 与
-19.1 LUFS 两档）。这里锁住三件事：

  1. 混音改成 normalize=0，不再随输入数改变人声电平；
  2. 混完按目标响度补固定增益，并且补完不超过真峰值上限；
  3. 集成测试跑一遍真实 ffmpeg 混流，成片响度确实落在目标附近。
"""

import math
import tempfile
import unittest
from pathlib import Path

from backend.app.providers.media_ffmpeg import generate_tone, run_ffmpeg
from backend.app.services.renderer import (
    MAX_TRUE_PEAK,
    TARGET_LUFS,
    VideoRenderer,
    loudness_gain,
    measure_loudness,
)


class LoudnessGainTests(unittest.TestCase):
    def test_quiet_mix_is_lifted_to_target(self):
        gain = loudness_gain({"input_i": -25.8, "input_tp": -20.0})
        self.assertAlmostEqual(gain, TARGET_LUFS - (-25.8), places=3)

    def test_true_peak_caps_the_gain(self):
        gain = loudness_gain({"input_i": -25.8, "input_tp": -8.76})
        self.assertAlmostEqual(gain, MAX_TRUE_PEAK - (-8.76), places=3)
        self.assertLessEqual(-8.76 + gain, MAX_TRUE_PEAK + 1e-9)

    def test_missing_measurement_keeps_original_level(self):
        self.assertEqual(loudness_gain({}), 0.0)
        self.assertEqual(loudness_gain({"input_i": -math.inf, "input_tp": -math.inf}), 0.0)


class MuxIntegrationTests(unittest.TestCase):
    def _silent_video(self, root: Path, seconds: float) -> Path:
        target = root / "silent.mp4"
        result = run_ffmpeg(
            [
                "-f", "lavfi", "-i", f"color=c=black:s=320x568:r=25",
                "-t", f"{seconds:.2f}", "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-an", str(target),
            ]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return target

    def test_mix_hits_target_loudness_without_clipping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = self._silent_video(root, 4.0)
            voice = root / "voice.wav"
            music = root / "music.wav"
            # 人声与音乐的电平按真实素材取：generate_tone 的 volume=1.0 约 -22 LUFS，
            # 贴近 edge-tts 的实际输出；BGM 再压低到 0.18 的轨道音量。
            generate_tone(voice, 3.6, 440, 1.0)
            generate_tone(music, 3.6, 220, 0.5)
            final = root / "final.mp4"
            info = VideoRenderer(root)._mux_audio(
                video, [(voice, 1.0), (music, 0.18)], final, 4.0,
                duck={"mode": "不压低"}, fps=25,
            )
            self.assertEqual(info["amix_normalize"], 0)
            self.assertGreater(info["gain_db"], 0)
            measured = measure_loudness(final)
            self.assertIsNotNone(measured["input_i"])
            self.assertAlmostEqual(measured["input_i"], TARGET_LUFS, delta=2.0)
            self.assertLessEqual(measured["input_tp"], -1.0)

    def test_voice_only_render_is_normalized_too(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = self._silent_video(root, 3.0)
            voice = root / "voice.wav"
            generate_tone(voice, 2.6, 440, 1.0)
            final = root / "final.mp4"
            VideoRenderer(root)._mux_audio(video, [(voice, 1.0)], final, 3.0, fps=25)
            measured = measure_loudness(final)
            self.assertAlmostEqual(measured["input_i"], TARGET_LUFS, delta=2.0)


if __name__ == "__main__":
    unittest.main()
