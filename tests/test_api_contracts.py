import unittest

from backend.app.services.uploads import validate_audio_upload


class UploadContractTests(unittest.TestCase):
    def setUp(self):
        self.task = {
            "input": {"style": "诗意纪实"},
            "shots": [{"id": "shot_1"}, {"id": "shot_2"}],
        }

    def test_voice_upload_requires_valid_shot_id(self):
        with self.assertRaisesRegex(ValueError, "shot_id"):
            validate_audio_upload({"kind": "voice"}, self.task)
        result = validate_audio_upload(
            {"kind": "voice", "shot_id": "shot_2"}, self.task
        )
        self.assertEqual(result["shot_id"], "shot_2")

    def test_music_upload_requires_license_confirmation(self):
        payload = {
            "kind": "music",
            "mood": "舒缓",
            "license": "team-owned",
            "author": "team",
            "usage_scope": "competition",
        }
        with self.assertRaisesRegex(ValueError, "approved=true"):
            validate_audio_upload(payload, self.task)
        payload["approved"] = True
        result = validate_audio_upload(payload, self.task)
        self.assertEqual(result["moods"], ["舒缓"])
        self.assertEqual(result["styles"], ["诗意纪实"])


if __name__ == "__main__":
    unittest.main()
