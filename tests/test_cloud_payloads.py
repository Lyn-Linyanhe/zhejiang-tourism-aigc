"""文生图 / 图生视频在没有密钥时拒绝生成。请求体检查不发 HTTP。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.providers.image import (
    CloudImageProvider,
    ImageConfigError,
    build_image_payload,
)
from backend.app.providers.video import (
    CloudVideoProvider,
    VideoConfigError,
    build_video_payload,
)
from backend.app.services.consistency import subject_lock as resolve_subject_lock


_IMAGE_ENV = ("IMAGE_BASE_URL", "IMAGE_API_KEY", "IMAGE_MODEL", "IMAGE_SIZE")
_VIDEO_ENV = (
    "VIDEO_BASE_URL",
    "VIDEO_API_KEY",
    "VIDEO_MODEL",
    "VIDEO_RESOLUTION",
    "VIDEO_RATIO",
)


class CloudPayloadTests(unittest.TestCase):
    def test_image_refuses_without_endpoint_and_payload_carries_seed(self):
        cleared = {name: "" for name in _IMAGE_ENV}
        with patch.dict(os.environ, cleared, clear=False):
            with self.assertRaises(ImageConfigError) as caught:
                CloudImageProvider(settings={})
        self.assertIn("IMAGE_BASE_URL", str(caught.exception))

        payload = build_image_payload(
            "西湖薄雾",
            model="local-test",
            size="1024x1536",
            seed=42,
            negative="文字，水印",
        )
        self.assertEqual(payload["seed"], 42)
        self.assertNotIn("urlopen", str(payload))
        locked = build_image_payload(
            "同一主体", model="local-test", size="1024x1536", seed=42,
            negative="文字", reference_image="data:image/png;base64,abc",
        )
        self.assertEqual(locked["image"], "data:image/png;base64,abc")

    def test_video_refuses_without_key_and_first_frame_is_an_image_item(self):
        cleared = {name: "" for name in _VIDEO_ENV}
        with patch.dict(os.environ, cleared, clear=False):
            with self.assertRaises(VideoConfigError) as caught:
                CloudVideoProvider(settings={})
        self.assertIn("VIDEO_API_KEY", str(caught.exception))

        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "first.png"
            frame.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-png")
            provider = CloudVideoProvider(
                settings={},
                base_url="https://example.invalid/api/v3",
                api_key="test-key-not-sent",
                model="test-model",
            )
            shot = {
                "id": "shot_1",
                "order": 1,
                "task_id": "task_payload",
                "visual_prompt": "断桥残雪，镜头缓慢前推",
                "first_frame_path": str(frame),
                "duration_sec": 5,
            }
            captured: dict = {}

            def _reject_submit(tasks_url, payload, api_key):
                captured["url"] = tasks_url
                captured["payload"] = payload
                captured["api_key"] = api_key
                raise AssertionError("测试不得发出 HTTP 请求")

            with patch(
                "backend.app.providers.video.submit_task",
                side_effect=_reject_submit,
            ):
                with self.assertRaises(AssertionError):
                    provider.generate(shot, directory, {"task_id": "task_payload"})

        payload = captured["payload"]
        self.assertEqual(payload["seed"], payload["seed"])
        self.assertIsInstance(payload["seed"], int)
        image_items = [
            item for item in payload["content"] if item.get("type") == "image_url"
        ]
        self.assertEqual(len(image_items), 1)
        self.assertEqual(image_items[0]["role"], "first_frame")
        self.assertTrue(image_items[0]["image_url"]["url"].startswith("data:image/"))
        bare = build_video_payload(
            model="m",
            prompt="p",
            image_url="",
            duration=5,
            resolution="720p",
            ratio="9:16",
            seed=7,
        )
        self.assertEqual(bare["seed"], 7)
        self.assertFalse(any(item.get("type") == "image_url" for item in bare["content"]))

    def test_image_provider_sends_the_same_subject_reference_with_the_locked_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "subject.png"
            reference.write_bytes(b"\x89PNG\r\n\x1a\nreference")
            provider = CloudImageProvider(
                settings={}, base_url="https://example.invalid/v1", model="test-image"
            )
            style_ctx = {
                "task_id": "task_lock",
                "city": "杭州",
                "landmark": "西湖",
                "subject_lock": "同一座断桥，石栏缺口和青灰石材保持不变",
                "subject_reference_path": str(reference),
                "image_reference_field": "image",
                "size": "1024x1536",
            }
            captured: dict = {}

            def _fake_request(url, payload, api_key):
                captured.update(url=url, payload=payload, api_key=api_key)
                return b"generated-image"

            with patch("backend.app.providers.image.request_image", side_effect=_fake_request):
                result = provider.generate(
                    {"id": "shot_1", "order": 1, "visual_prompt": "断桥侧面，清晨薄雾"},
                    directory,
                    style_ctx,
                )
            lock = resolve_subject_lock(style_ctx)
            self.assertIn(lock["id"], captured["payload"]["prompt"])
            self.assertIn("同一座断桥", captured["payload"]["prompt"])
            self.assertTrue(captured["payload"]["image"].startswith("data:image/png;base64,"))
            self.assertEqual(result.event["subject_id"], lock["id"])
            self.assertTrue(result.event["subject_reference_used"])


if __name__ == "__main__":
    unittest.main()
