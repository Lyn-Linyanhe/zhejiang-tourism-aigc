import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.models import Shot
from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.audio_sync import PerShotAudioBuilder
from backend.app.services.renderer import VideoRenderer


class EndToEndSyncTests(unittest.TestCase):
    def test_cues_drive_srt_video_duration_and_ducking_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(
                data_dir=root / "runtime",
                media_dir=root / "media",
                tts_base_url="",
                tts_api_key="",
                tts_model="",
            )
            voice_a = root / "a.wav"
            voice_b = root / "b.wav"
            music = root / "music.wav"
            generate_tone(voice_a, 0.7, 440, 0.1)
            generate_tone(voice_b, 0.8, 520, 0.1)
            generate_tone(music, 0.4, 220, 0.1)
            asset = root / "scene.ppm"
            asset.write_bytes(b"P6\n2 2\n255\n" + bytes((20, 90, 120)) * 4)
            shots = [
                Shot("shot_1", 1, 1, "A", "A", "甲", "字幕甲", asset_local_path=str(asset), audio_local_path=str(voice_a)),
                Shot("shot_2", 2, 1, "B", "B", "乙", "字幕乙", asset_local_path=str(asset), audio_local_path=str(voice_b)),
            ]
            audio = PerShotAudioBuilder(settings, None).build(
                "task_e2e", shots, voice="女声", rate=1, target_duration=30
            ).to_dict()
            audio.update(
                {
                    "voice_local_path": audio["voice_path"],
                    "music_local_path": str(music),
                }
            )
            rendered = VideoRenderer(settings.media_dir).render(
                "task_e2e", [shot.__dict__ for shot in shots], audio,
                True, True, "720x1280", 25, 1.0, 0.18,
            )
            self.assertEqual(rendered["mix_strategy"], "sidechaincompress-asplit-2")
            self.assertLessEqual(abs(rendered["total_duration"] - audio["total_duration"]), 0.05)
            srt = (settings.media_dir / "task_e2e" / "subtitles.srt").read_text(encoding="utf-8")
            self.assertIn("字幕甲", srt)
            self.assertIn("00:00:00,000", srt)


if __name__ == "__main__":
    unittest.main()
