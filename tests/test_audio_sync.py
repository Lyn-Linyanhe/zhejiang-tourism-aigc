import tempfile
import unittest
import subprocess
from pathlib import Path
from types import SimpleNamespace

from backend.app.models import Shot
from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.audio_sync import PerShotAudioBuilder, probe_audio


class AudioSyncTests(unittest.TestCase):
    def test_no_voice_keeps_nominal_target_timing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(
                data_dir=root / "runtime", media_dir=root / "media",
                tts_base_url="", tts_api_key="", tts_model="",
            )
            shots = [Shot("shot_1", 1, 15, "A", "A", "", "字幕")]
            result = PerShotAudioBuilder(settings, None).build(
                "task_silent", shots, voice="无配音", rate=1, target_duration=15
            )
            self.assertEqual(result.total_duration, 15)
            self.assertEqual(result.timing_source, "nominal-no-voice")
            self.assertEqual(result.cues, [])

    def test_uploaded_shots_generate_measured_cues_and_48k_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(
                data_dir=root / "runtime",
                media_dir=root / "media",
                tts_base_url="",
                tts_api_key="",
                tts_model="",
            )
            first = root / "first.wav"
            second = root / "second.wav"
            generate_tone(first, 0.73, 440, 0.1)
            generate_tone(second, 0.91, 520, 0.1)
            mono = root / "first-mono.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(first),
                 "-ac", "1", str(mono)], check=True,
            )
            self.assertEqual(probe_audio(mono)["channels"], 1)
            old_silence = settings.media_dir / "task_test" / "silence-150ms.wav"
            old_silence.parent.mkdir(parents=True)
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "anullsrc=r=48000:cl=mono", "-t", "0.150", str(old_silence)],
                check=True,
            )
            shots = [
                Shot("shot_1", 1, 1, "A", "A", "第一句", "字幕一", audio_local_path=str(mono)),
                Shot("shot_2", 2, 1, "B", "B", "第二句", "字幕二", audio_local_path=str(second)),
            ]
            result = PerShotAudioBuilder(settings, None).build(
                "task_test", shots, voice="女声", rate=1.0, target_duration=30
            )
            self.assertEqual(result.timing_source, "measured-shot-audio")
            self.assertEqual(len(result.cues), 2)
            self.assertAlmostEqual(result.cues[1].start - result.cues[0].end, 0.150, places=3)
            self.assertEqual(result.cues[0].text, "字幕一")
            info = probe_audio(Path(result.voice_path))
            self.assertEqual(info["sample_rate"], 48000)
            self.assertEqual(info["channels"], 2)
            self.assertEqual(probe_audio(old_silence)["channels"], 2)
            self.assertLessEqual(abs(result.total_duration - sum(shot.duration_sec for shot in shots)), 0.001)


if __name__ == "__main__":
    unittest.main()
