"""配音自然度的两条底线。

1. 隔离音频运行时缺失时，edge-tts 必须还能跑（否则会悄悄降级成 Windows SAPI
   的机械音，用户一耳朵就能听出来）。
2. 神经 TTS 每段自带的首尾静音要被裁掉，再由固定常量补句间/镜间停顿，
   否则节奏取决于引擎内边距，语流会拖沓且不可控。
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.models import Shot
from backend.app.providers.media_ffmpeg import generate_tone, run_ffmpeg
from backend.app.providers.tts import generate_edge_tts
from backend.app.services.audio_sync import (
    SENTENCE_GAP_SEC,
    PerShotAudioBuilder,
    probe_audio,
)


class FakeCommunicate:
    def __init__(self, text, voice, rate=None, pitch=None):
        self.text, self.voice, self.rate, self.pitch = text, voice, rate, pitch
        self.saved = ""

    async def save(self, path):
        Path(path).write_bytes(b"id3" + b"\0" * 4096)
        self.saved = path


class EdgeTTSFallbackTests(unittest.TestCase):
    def test_uses_in_process_edge_when_runtime_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "voice.mp3"
            fake = types.SimpleNamespace(Communicate=FakeCommunicate)
            with patch.dict(sys.modules, {"edge_tts": fake}):
                result = generate_edge_tts("西湖的清晨。", out, "晓晓·温暖女声")
            self.assertEqual(result["provider"], "edge-tts")
            self.assertEqual(result["transport"], "in-process")
            self.assertTrue(out.is_file())
            self.assertEqual(result["model"], "zh-CN-XiaoxiaoNeural")

    def test_uses_runtime_when_it_exists(self):
        class FakeRuntime:
            def __init__(self):
                self.calls = []

            def available(self):
                return True

            def synthesize_edge_tts(self, text, output_path, voice, rate, pitch):
                self.calls.append((text, voice, rate, pitch))
                Path(output_path).write_bytes(b"x" * 2048)
                return {"path": str(output_path), "model": voice, "transport": "runtime"}

        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "voice.mp3"
            result = generate_edge_tts("西湖的清晨。", out, "晓晓·温暖女声", 1.0, runtime)
        self.assertEqual(runtime.calls[0][1], "zh-CN-XiaoxiaoNeural")
        self.assertEqual(result["transport"], "runtime")


class SegmentPacingTests(unittest.TestCase):
    def _padded_tone(self, root: Path) -> Path:
        tone = root / "tone.wav"
        # 振幅按真实语音峰值取，太小会被 -45dB 阈值当成静音整段裁掉
        generate_tone(tone, 1.0, 440, 0.18)
        padded = root / "padded.wav"
        completed = run_ffmpeg(
            [
                "-i", str(tone),
                "-af", "adelay=600:all=1,apad=pad_dur=0.7",
                "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                str(padded),
            ]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return padded

    def test_engine_padding_is_trimmed_and_sentence_gap_inserted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            padded = self._padded_tone(root)
            self.assertAlmostEqual(probe_audio(padded)["duration"], 2.3, places=1)

            settings = types.SimpleNamespace(
                data_dir=root / "runtime", media_dir=root / "media",
                tts_base_url="", tts_api_key="", tts_model="",
            )
            shots = [Shot("shot_1", 1, 6, "A", "A", "第一句。第二句。", "第一句。第二句。")]
            builder = PerShotAudioBuilder(settings, None)
            with patch.object(
                builder, "_synthesize_cached",
                return_value=(padded, {"provider": "test-tone", "model": "test-only"}),
            ):
                result = builder.build("task_pace", shots, voice="晓晓·温暖女声", rate=1.0, target_duration=6)

            first, second = result.cues
            # 0.6s 前导 + 0.7s 尾部静音被裁掉，只剩 1.0s 人声 + 两端各 50ms
            self.assertLess(first.end - first.start, 1.4)
            self.assertGreater(first.end - first.start, 0.9)
            # 句间停顿由常量决定，不再依赖引擎内边距
            self.assertAlmostEqual(second.start - first.end, SENTENCE_GAP_SEC, places=3)

    def test_uploaded_audio_is_never_trimmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uploaded = root / "uploaded.wav"
            generate_tone(uploaded, 1.0, 440, 0.02)
            padded = self._padded_tone(root)
            settings = types.SimpleNamespace(
                data_dir=root / "runtime", media_dir=root / "media",
                tts_base_url="", tts_api_key="", tts_model="",
            )
            shot = Shot("shot_1", 1, 3, "A", "A", "用户原声", "用户原声", audio_local_path=str(padded))
            result = PerShotAudioBuilder(settings, None).build(
                "task_upload", [shot], voice="上传配音音频", rate=1.0,
                target_duration=3, force_provider="upload",
            )
            self.assertAlmostEqual(result.cues[0].end - result.cues[0].start, 2.3, places=1)


if __name__ == "__main__":
    unittest.main()
