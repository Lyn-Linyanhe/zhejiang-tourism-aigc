"""Source metadata survives supplemental provider and render boundaries."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.providers.media import PexelsAssetProvider


class ExternalAssetTests(unittest.TestCase):
    def test_pexels_keeps_actual_query_and_source_as_preview_candidate(self):
        photo = {"id": 12, "url": "https://www.pexels.com/photo/12/", "photographer": "Photographer", "alt": "West Lake", "src": {"portrait": "https://images.pexels.com/photo.jpg"}}
        # 视频优先：第一个请求是 /videos/search（这里无视频可下），回退照片检索与下载。
        responses = [
            io.BytesIO(json.dumps({"videos": []}).encode()),
            io.BytesIO(json.dumps({"photos": [photo]}).encode()),
            io.BytesIO(bytes((0xFF, 0xD8, 0xFF)) + b"j" * 1200),  # JPEG 魔数：合并后的下载校验要求真实图片头
        ]
        with tempfile.TemporaryDirectory() as directory, patch("backend.app.providers.media.urllib.request.urlopen", side_effect=responses) as request:
            result = PexelsAssetProvider("test-key").create_or_find(
                "task", {"id": "shot", "order": 1, "search_query": "西湖; West Lake Hangzhou", "entity_id": "hz-xihu", "shot_tag": "wide"},
                Path(directory), "#123456", "#abcdef",
            )
        self.assertIn("query=West+Lake+Hangzhou", request.call_args_list[0].args[0].full_url)
        self.assertEqual(result["provenance"]["search_query"], "West Lake Hangzhou")
        self.assertEqual(result["provenance"]["source_url"], photo["url"])
        self.assertEqual(result["provenance"]["entity_id"], "hz-xihu")
        self.assertEqual(result["provenance"]["classification"], "external-candidate")
        self.assertEqual(result["provenance"]["allowed_usages"], ["preview"])
        self.assertEqual(result["media_type"], "image")


if __name__ == "__main__":
    unittest.main()
