import json
import tempfile
import unittest
from pathlib import Path

from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.providers.music import select_music


class MusicCatalogTests(unittest.TestCase):
    def test_only_approved_exact_mood_and_style_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            track = root / "licensed.wav"
            generate_tone(track, 1, 220, 0.1)
            catalog = root / "catalog.json"
            catalog.write_text(
                json.dumps(
                    {
                        "tracks": [
                            {
                                "id": "licensed",
                                "file": track.name,
                                "moods": ["舒缓"],
                                "styles": ["诗意纪实"],
                                "license": "team-owned",
                                "author": "team",
                                "usage_scope": "competition",
                                "approved": True,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selected = select_music(
                task_audio={}, mood="舒缓", style="诗意纪实", duration=2,
                catalog_path=catalog, music_dir=root,
            )
            self.assertEqual(selected.id, "licensed")
            with self.assertRaisesRegex(RuntimeError, "No approved music"):
                select_music(
                    task_audio={}, mood="热烈", style="诗意纪实", duration=2,
                    catalog_path=catalog, music_dir=root,
                )


if __name__ == "__main__":
    unittest.main()
