"""Pexels 视频优先检索与静图降级的行为测试，不访问网络。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.providers.media import PexelsAssetProvider


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _fake_urlopen(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    if "/videos/search" in url:
        return _Response(json.dumps({
            "videos": [{
                "id": 101,
                "url": "https://www.pexels.com/video/101",
                "user": {"name": "Video Author"},
                "video_files": [
                    {"quality": "sd", "width": 540, "height": 960, "link": "https://media.test/video-sd.mp4"},
                    {"quality": "hd", "width": 1080, "height": 1920, "link": "https://media.test/video-hd.mp4"},
                    {"quality": "hd", "width": 1920, "height": 1080, "link": "https://media.test/video-landscape.mp4"},
                ],
            }]
        }).encode("utf-8"))
    if "/search" in url:
        return _Response(json.dumps({
            "photos": [{
                "id": 202,
                "url": "https://www.pexels.com/photo/202",
                "photographer": "Photo Author",
                "src": {"portrait": "https://media.test/photo.jpg"},
            }]
        }).encode("utf-8"))
    if "media.test" in url:
        if "video" in url:
            return _Response(b"\x00\x00\x00\x18ftypisom" + b"x" * 2048)
        return _Response(b"\xff\xd8\xff" + b"x" * 2048)
    raise AssertionError(f"unexpected URL: {url}")


def _fake_no_videos(request, timeout=0):
    url = request.full_url if hasattr(request, "full_url") else str(request)
    if "/videos/search" in url:
        return _Response(b'{"videos":[]}')
    return _fake_urlopen(request, timeout)


class PexelsMediaTests(unittest.TestCase):
    def test_video_is_preferred_and_labelled_as_video(self):
        provider = PexelsAssetProvider("test-key")
        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.app.providers.media.urllib.request.urlopen", side_effect=_fake_urlopen
        ):
            result = provider.create_or_find(
                "task_pexels",
                {"id": "shot_1", "order": 1, "search_query": "West Lake Hangzhou"},
                Path(directory),
                "#2d6c63",
                "#d6ad60",
                {"output_resolution": "720x1280"},
            )
            self.assertEqual(result["media_type"], "video")
            self.assertEqual(result["model"], "pexels-video-search")
            self.assertTrue(result["local_path"].endswith("shot_1.mp4"))
            self.assertTrue(Path(result["local_path"]).is_file())
            self.assertEqual(result["provider_metadata"]["pexels_id"], 101)
            self.assertEqual(result["provider_metadata"]["width"], 1080)
            self.assertEqual(result["provider_metadata"]["height"], 1920)

    def test_photo_fallback_is_explicitly_reported_as_image(self):
        provider = PexelsAssetProvider("test-key")
        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.app.providers.media.urllib.request.urlopen", side_effect=_fake_no_videos
        ):
            result = provider.create_or_find(
                "task_pexels",
                {"id": "shot_2", "order": 2, "search_query": "West Lake Hangzhou"},
                Path(directory),
                "#2d6c63",
                "#d6ad60",
                {"output_resolution": "720x1280"},
            )
            self.assertEqual(result["media_type"], "image")
            self.assertEqual(result["model"], "pexels-photo-search")
            self.assertTrue(result["local_path"].endswith("shot_2.jpg"))

    def test_video_only_fails_instead_of_silently_returning_a_photo(self):
        provider = PexelsAssetProvider("test-key", media_mode="video-only")
        with tempfile.TemporaryDirectory() as directory, patch(
            "backend.app.providers.media.urllib.request.urlopen", side_effect=_fake_no_videos
        ):
            with self.assertRaisesRegex(RuntimeError, "video"):
                provider.create_or_find(
                    "task_pexels",
                    {"id": "shot_3", "order": 3, "search_query": "West Lake Hangzhou"},
                    Path(directory),
                    "#2d6c63",
                    "#d6ad60",
                    {"output_resolution": "720x1280"},
                )


if __name__ == "__main__":
    unittest.main()
