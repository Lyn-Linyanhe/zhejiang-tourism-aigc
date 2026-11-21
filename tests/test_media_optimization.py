import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.providers.music import authorized, select_music
from backend.app.providers.stock import StockProvider
from backend.app.services.subtitle_alignment import align_subtitles
from backend.app.services.uploads import validate_audio_upload


class OptimizationTests(unittest.TestCase):
    def test_stock_deduplicates_then_falls_back_to_a_photo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "test.mp4"
            photo = root / "test.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=64x64:d=0.3",
                    str(video),
                ],
                check=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    str(photo),
                ],
                check=True,
            )

            def search(platform, key, base, q, kind, day):
                row = dict(
                    id=1,
                    pageURL="https://pixabay.com/videos/1",
                    tags=["Hangzhou", "West Lake"],
                    user="test",
                )
                if kind == "video":
                    row.update(
                        duration=5,
                        videos={
                            "small": {
                                "width": 64,
                                "height": 64,
                                "url": "https://example.test/clip",
                            }
                        },
                    )
                else:
                    row.update(
                        imageWidth=64,
                        imageHeight=64,
                        largeImageURL="https://example.test/photo",
                    )
                return [row]

            def get(url, headers):
                return io.BytesIO(
                    (video if url.endswith("clip") else photo).read_bytes()
                )

            provider = StockProvider([("pixabay", "fixture", "")])
            ctx = {"stock_ids": set(), "output_resolution": "720x1280"}
            shot = dict(
                id="s1",
                city="杭州",
                landmark="西湖",
                landmark_aliases=["West Lake"],
                duration_sec=4,
            )
            with (
                patch("backend.app.providers.stock.search", side_effect=search),
                patch("backend.app.providers.stock._get", side_effect=get),
            ):
                first = provider.create_or_find("task", shot, root, "", "", ctx)
                second = provider.create_or_find(
                    "task", dict(shot, id="s2"), root, "", "", ctx
                )
                with self.assertRaisesRegex(RuntimeError, "独立镜头"):
                    provider.create_or_find(
                        "task", dict(shot, id="s3"), root, "", "", ctx
                    )
            self.assertEqual(
                [first["media_type"], second["media_type"]], ["video", "image"]
            )
            self.assertIn("West Lake", first["location_evidence"])
            self.assertEqual(len(ctx["stock_ids"]), 2)

    def test_music_unknown_even_with_old_approval(self):
        item = dict(
            license="用户自备素材，授权状态未知",
            author="用户提供",
            usage_scope="比赛",
            approved=True,
            moods=["舒缓"],
            styles=["诗意纪实"],
            kind="music",
            mood="舒缓",
        )
        self.assertFalse(authorized(item))
        with self.assertRaises(ValueError):
            validate_audio_upload(item, {"input": {"style": "诗意纪实"}})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "unknown.mp3"
            path.touch()
            (root / "catalog.json").write_text(json.dumps([dict(item, file=path.name)]))
            with self.assertRaisesRegex(RuntimeError, "No approved music"):
                select_music(
                    task_audio={"music_upload": dict(item, path=str(path))},
                    mood="舒缓",
                    style="诗意纪实",
                    duration=30,
                    catalog_path=root / "catalog.json",
                    music_dir=root,
                )

    def test_whisperx_verified_segments_survive_whisper_failure(self):
        runtime = Mock()
        runtime.align.return_value = {"segments": [{"words": [
            {"text": "湖", "start": 0.2, "end": 0.65, "score": 0.98}]}]}
        runtime.transcribe.side_effect = TimeoutError()
        audio = dict(voice_path="test.wav", cues=[
            dict(shot_id="a", text="湖。", start=0, end=1),
            dict(shot_id="b", text="山。", start=1, end=2)])
        align_subtitles(audio, runtime)
        self.assertEqual([s["source"] for s in audio["subtitle_alignment"]["segments"]], ["whisperx","sentence"])
        self.assertEqual(audio["subtitle_cues"][0]["start"], 0.2)
        self.assertEqual(audio["subtitle_cues"][1], audio["cues"][1])

    def test_alignment_preserves_edited_caption_and_gaps(self):
        runtime = Mock()
        runtime.align.side_effect = RuntimeError("WhisperX unavailable")
        runtime.transcribe.return_value = {
            "segments": [
                {
                    "words": [
                        {"text": "西湖", "start": 0.1, "end": 0.6},
                        {"text": "真美", "start": 0.8, "end": 1.4},
                    ]
                }
            ]
        }
        audio = dict(
            voice_path="fixture.wav",
            cues=[
                dict(shot_id="a", text="西湖，真美。", start=0, end=2),
                dict(shot_id="b", text="看山。", start=2.15, end=4),
            ],
            provider_trace=[{"text": "西湖，真美。"}, {"text": "远山如画。"}],
        )
        align_subtitles(audio, runtime)
        self.assertEqual(
            [w["source"] for w in audio["subtitle_alignment"]["segments"]],
            ["whisper", "sentence"],
        )
        self.assertEqual(
            [c["text"] for c in audio["subtitle_cues"]], ["西湖，", "真美。", "看山。"]
        )
        self.assertEqual(audio["subtitle_cues"][0]["start"], 0.1)
        self.assertEqual(audio["subtitle_cues"][-1], audio["cues"][-1])
