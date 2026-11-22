"""参考图查找的离线测试。

参考图只用于图生图的输入，不输出原图，所以这里不校验许可，只校验匹配正确性：
同城同点位才给图，跨城市必须给空，同 seed 稳定、不同 seed 分散。

参考图本身（322 张 webp / 101MB）走 Git LFS，不随代码仓库分发。缺图时匹配类
断言无从谈起，这些用例跳过并说明原因；有图时照常严格校验。跨城市、未知点位
这些「必须给空」的用例不依赖图片，任何环境都跑。
"""

import unittest

from backend.app.services import reference_images as ri

NO_IMAGES = (
    "参考图未随仓库分发（Git LFS，322 张 / 101MB），"
    "见 docs/research/xhs-zhejiang-image-index.csv"
)


def _has_images() -> bool:
    return ri.stats()["images"] > 0


class ReferenceImageTests(unittest.TestCase):
    def setUp(self):
        ri.reset_cache()

    @unittest.skipUnless(_has_images(), NO_IMAGES)
    def test_index_is_loaded(self):
        stats = ri.stats()
        self.assertGreater(stats["images"], 0, "图片索引为空，参考图功能会静默失效")
        self.assertGreaterEqual(stats["cities"], 11)

    @unittest.skipUnless(_has_images(), NO_IMAGES)
    def test_same_city_and_spot_returns_an_existing_file(self):
        import pathlib

        path = ri.find_reference(city="杭州", spot="西湖", shot_tag="season", seed="shot_1")
        self.assertTrue(path, "西湖应当有参考图")
        self.assertTrue(pathlib.Path(path).is_file())

    def test_unknown_spot_returns_empty(self):
        """未知点位不能随便配图：配错构图比没有参考更糟。"""
        self.assertEqual(ri.find_reference(city="杭州", spot="完全不存在的点位"), "")

    def test_cross_city_never_leaks(self):
        """杭州没有的点位不能返回别的城市的图。"""
        self.assertEqual(ri.find_reference(city="湖州", spot="西湖"), "")

    def test_same_seed_is_stable(self):
        a = ri.find_reference(city="绍兴", spot="兰亭", shot_tag="landmark", seed="shot_1")
        b = ri.find_reference(city="绍兴", spot="兰亭", shot_tag="landmark", seed="shot_1")
        self.assertEqual(a, b, "同一镜重跑必须挑到同一张参考，否则不可复现")

    @unittest.skipUnless(_has_images(), NO_IMAGES)
    def test_different_seeds_spread(self):
        """不同镜头应挑到不同参考图，避免整片雷同。"""
        paths = {
            ri.find_reference(city="杭州", spot="西湖", shot_tag="season", seed=f"shot_{i}")
            for i in range(1, 5)
        }
        self.assertGreater(len(paths), 1, "四个镜头挑到同一张图，参考意义有限")

    @unittest.skipUnless(_has_images(), NO_IMAGES)
    def test_shot_tag_preference_does_not_break_fallback(self):
        """tag 对不上时仍应给同点位图，而不是直接返回空。"""
        path = ri.find_reference(city="杭州", spot="西湖", shot_tag="完全不存在的tag", seed="s")
        self.assertTrue(path)

    def test_empty_city_and_spot_returns_empty(self):
        self.assertEqual(ri.find_reference(city="", spot=""), "")


class RegistryEditDispatchTests(unittest.TestCase):
    """registry 应当优先走 edit()，没有参考图时才回落到 generate()。"""

    def test_edit_is_preferred_when_reference_exists(self):
        import pathlib
        import tempfile

        from backend.app.providers.registry import ProviderRegistry, ProviderSpec

        calls = []

        class FakeProvider:
            def edit(self, shot, reference, output_dir, style_ctx):
                calls.append(("edit", reference))
                raise RuntimeError("stop-here")

            def generate(self, shot, output_dir, style_ctx):
                calls.append(("generate", ""))
                raise RuntimeError("stop-here")

        registry = ProviderRegistry()
        registry.register(ProviderSpec(
            slot="asset", name="fake", label="", factory=lambda: FakeProvider(),
            available=lambda: True,
        ))
        FakeProvider.kind = "image"
        with tempfile.TemporaryDirectory() as tmp:
            ref = pathlib.Path(tmp) / "ref.png"
            ref.write_bytes(b"0" * 32)
            with self.assertRaises(RuntimeError):
                registry.generate_asset(
                    "fake", "t1", {"id": "shot_1", "reference_image_path": str(ref)},
                    pathlib.Path(tmp), {},
                )
        self.assertEqual(calls[0][0], "edit", "有参考图时必须走 edit")

    def test_generate_when_no_reference(self):
        import pathlib
        import tempfile

        from backend.app.providers.registry import ProviderRegistry, ProviderSpec

        calls = []

        class FakeProvider:
            def edit(self, *args, **kwargs):
                calls.append("edit")
                raise RuntimeError("should not be called")

            def generate(self, shot, output_dir, style_ctx):
                calls.append("generate")
                raise RuntimeError("stop-here")

        registry = ProviderRegistry()
        registry.register(ProviderSpec(
            slot="asset", name="fake", label="", factory=lambda: FakeProvider(),
            available=lambda: True,
        ))
        FakeProvider.kind = "image"
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                registry.generate_asset("fake", "t1", {"id": "shot_1"}, pathlib.Path(tmp), {})
        self.assertEqual(calls[0], "generate", "没有参考图时必须走 generate")


    def test_video_provider_ignores_reference(self):
        """参考图只对静帧生成有意义；视频 provider 必须忽略，否则整条链会被打断。"""
        import pathlib
        import tempfile

        from backend.app.providers.registry import ProviderRegistry, ProviderSpec

        calls = []

        class FakeVideo:
            kind = "video"

            def edit(self, *args, **kwargs):
                calls.append("edit")
                raise RuntimeError("video 不该走 edit")

            def generate(self, shot, output_dir, style_ctx):
                calls.append("generate")
                raise RuntimeError("stop-here")

        registry = ProviderRegistry()
        registry.register(ProviderSpec(
            slot="asset", name="fake-video", label="", factory=lambda: FakeVideo(),
            available=lambda: True,
        ))
        with tempfile.TemporaryDirectory() as tmp:
            ref = pathlib.Path(tmp) / "ref.png"
            ref.write_bytes(b"0" * 32)
            with self.assertRaises(RuntimeError):
                registry.generate_asset(
                    "fake-video", "t1",
                    {"id": "shot_1", "reference_image_path": str(ref)},
                    pathlib.Path(tmp), {},
                )
        self.assertEqual(calls[0], "generate", "视频 provider 必须忽略参考图走 generate")


if __name__ == "__main__":
    unittest.main()
