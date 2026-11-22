"""图生图与视频网关的离线单元测试。

全部不联网：只验证请求体构造、参数校验与轮询状态机。
真实调用在 docs/research/media-generation-acceptance.md 里有实测记录。
"""

import unittest
from unittest import mock

from backend.app.providers import video_gateway
from backend.app.providers.image import build_edit_payload, image_file_to_data_url


class ImageEditPayloadTests(unittest.TestCase):
    def test_edit_payload_requires_data_uri(self):
        """网关只吃 data URI：URL 和裸 base64 都不该被放行。"""
        with self.assertRaises(ValueError):
            build_edit_payload("改成黄昏", model="m", image_data_url="http://a/b.png")
        with self.assertRaises(ValueError):
            build_edit_payload("改成黄昏", model="m", image_data_url="iVBORw0KGgo=")

    def test_edit_payload_shape(self):
        payload = build_edit_payload(
            "改成黄昏", model="raccoon/gpt-image-2",
            image_data_url="data:image/png;base64,AAAA",
        )
        self.assertEqual(payload["model"], "raccoon/gpt-image-2")
        self.assertEqual(payload["quality"], "high")
        self.assertTrue(payload["image"].startswith("data:image/png;base64,"))
        # 网关不接受 multipart，所以请求体里不该出现文件字段
        self.assertNotIn("file", payload)

    def test_image_file_to_data_url(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "ref.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            url = image_file_to_data_url(path)
        self.assertTrue(url.startswith("data:image/png;base64,"))
        self.assertGreater(len(url), 40)

    def test_empty_reference_file_is_rejected(self):
        import pathlib
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "empty.png"
            path.write_bytes(b"")
            with self.assertRaises(RuntimeError):
                image_file_to_data_url(path)


class VideoGatewayValidationTests(unittest.TestCase):
    def test_rejects_empty_prompt(self):
        with self.assertRaises(video_gateway.VideoGatewayError):
            video_gateway.submit("http://x/v1", "k", prompt="   ", seconds=5)

    def test_rejects_out_of_range_seconds(self):
        for seconds in (3, 16, 0):
            with self.assertRaises(video_gateway.VideoGatewayError):
                video_gateway.submit("http://x/v1", "k", prompt="湖面", seconds=seconds)

    def test_rejects_too_long_prompt(self):
        with self.assertRaises(video_gateway.VideoGatewayError):
            video_gateway.submit("http://x/v1", "k", prompt="字" * 2001, seconds=5)

    def test_rejects_non_data_uri_image(self):
        with self.assertRaises(video_gateway.VideoGatewayError):
            video_gateway.submit(
                "http://x/v1", "k", prompt="湖面", seconds=5,
                image_data_url="http://a/b.png",
            )


class VideoGatewayPollingTests(unittest.TestCase):
    def test_poll_returns_on_completed(self):
        responses = [
            {"status": "queued"},
            {"status": "in_progress"},
            {"status": "completed", "url": "http://x/v.mp4"},
        ]
        with mock.patch.object(video_gateway, "_request", side_effect=responses):
            final = video_gateway.poll("http://x/v1", "k", "vid_1", interval=0, max_wait=10, sleep=lambda _: None)
        self.assertEqual(final["status"], "completed")

    def test_poll_raises_on_failed(self):
        with mock.patch.object(video_gateway, "_request", return_value={"status": "failed", "error": "配额不足"}):
            with self.assertRaises(video_gateway.VideoGatewayError) as ctx:
                video_gateway.poll("http://x/v1", "k", "vid_1", interval=0, max_wait=10, sleep=lambda _: None)
        self.assertIn("配额不足", str(ctx.exception))

    def test_poll_times_out_without_resubmitting(self):
        """超时必须抛错并提示不要重复提交——重提等于重复扣费。"""
        calls = []

        def fake_request(*args, **kwargs):
            calls.append(kwargs.get("method"))
            return {"status": "in_progress"}

        with mock.patch.object(video_gateway, "_request", side_effect=fake_request):
            with self.assertRaises(video_gateway.VideoGatewayError) as ctx:
                video_gateway.poll(
                    "http://x/v1", "k", "vid_1", interval=0, max_wait=0, sleep=lambda _: None
                )
        self.assertIn("不要重复提交", str(ctx.exception))
        self.assertTrue(all(method == "GET" for method in calls), "轮询期间不应发起 POST")


class VideoGatewayDownloadTests(unittest.TestCase):
    def test_empty_download_is_failure(self):
        import pathlib
        import tempfile

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b""

        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "v.mp4"
            with mock.patch.object(video_gateway.urllib.request, "urlopen", return_value=FakeResponse()):
                with self.assertRaises(video_gateway.VideoGatewayError):
                    video_gateway.download("http://x/v.mp4", "k", target)
            self.assertFalse(target.exists())


class VideoApiStyleTests(unittest.TestCase):
    def test_detects_gateway_style(self):
        from backend.app.providers.video import _detect_api_style

        self.assertEqual(_detect_api_style("http://127.0.0.1:8787/v1"), "gateway")
        self.assertEqual(_detect_api_style("https://ark.cn-beijing.volces.com/api/v3"), "volcengine")
        self.assertEqual(_detect_api_style("https://x.tokendance.space/v1"), "tokendance")


if __name__ == "__main__":
    unittest.main()
