"""曲库覆盖面与浏览接口的回归。

背景：原来的 catalog 只覆盖 18 首、前端只提供「无音乐」一个选项，用户只能手填
风格词；而曲库标签是「国风雅韵」这类标准值，手填「国风」就选不到曲子。这批测试
锁住两件事：
  1. 前端暴露的 5 个风格 × 4 个情绪，每个组合都必须能选出已授权曲目；
  2. 浏览接口只返回已授权且文件真实存在的曲目。
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.services.catalog import get_music_library, resolve_music_track

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "runtime/music/catalog.json"
MUSIC_DIR = ROOT / "runtime/music"

STYLES = ["诗意纪实", "城市漫游", "国风雅韵", "青春活力", "节庆热烈"]
MOODS = ["舒缓", "雅致", "轻快", "热烈"]

NO_TRACKS = (
    "曲库未随仓库分发（Git LFS，47 首 / 196MB）。"
    "catalog.json 里登记了曲目，音频文件需另行获取。"
)


def _has_tracks() -> bool:
    if not CATALOG.is_file():
        return False
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    return any(
        (MUSIC_DIR / str(t.get("file"))).is_file()
        for t in payload.get("tracks", [])
    )


class MusicCoverageTests(unittest.TestCase):
    def test_catalog_covers_every_style_and_mood_pair(self):
        """5 风格 × 4 情绪 = 20 个组合，每个都要能选出曲目。"""
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        tracks = [t for t in payload["tracks"] if t.get("approved")]
        self.assertGreaterEqual(len(tracks), 20, "曲库中已授权曲目太少，覆盖不了组合")

        for style in STYLES:
            for mood in MOODS:
                matched = [
                    t for t in tracks
                    if mood in (t.get("moods") or []) and style in (t.get("styles") or [])
                ]
                self.assertTrue(matched, f"{style}+{mood} 没有可用曲目")

    @unittest.skipUnless(_has_tracks(), NO_TRACKS)
    def test_catalog_files_exist_on_disk(self):
        """catalog 里登记的文件必须真的在 music 目录里，否则选到就是空路径。"""
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        missing = [
            t.get("file") for t in payload["tracks"]
            if t.get("approved") and not (MUSIC_DIR / str(t.get("file"))).is_file()
        ]
        self.assertEqual(missing, [], f"catalog 登记但文件缺失: {missing}")


class MusicLibraryEndpointTests(unittest.TestCase):
    @unittest.skipUnless(_has_tracks(), NO_TRACKS)
    def test_library_lists_only_approved_existing_tracks(self):
        library = get_music_library(CATALOG, MUSIC_DIR)
        self.assertGreater(library["total"], 0)
        self.assertEqual(sorted(library["moods"]), sorted(MOODS))
        self.assertEqual(sorted(library["styles"]), sorted(STYLES))
        for track in library["tracks"]:
            self.assertTrue((MUSIC_DIR / track["file"]).is_file())

    def test_library_skips_unapproved_and_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.mp3").write_bytes(b"x")
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({
                "tracks": [
                    {"id": "keep", "file": "keep.mp3", "moods": ["舒缓"],
                     "styles": ["诗意纪实"], "approved": True},
                    {"id": "draft", "file": "keep.mp3", "moods": ["舒缓"],
                     "styles": ["诗意纪实"], "approved": False},
                    {"id": "gone", "file": "missing.mp3", "moods": ["舒缓"],
                     "styles": ["诗意纪实"], "approved": True},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            library = get_music_library(catalog, root)
            self.assertEqual([t["id"] for t in library["tracks"]], ["keep"])

    def test_library_survives_missing_catalog(self):
        library = get_music_library(ROOT / "runtime/music/does-not-exist.json", MUSIC_DIR)
        self.assertEqual(library["total"], 0)
        self.assertEqual(library["tracks"], [])


class MusicPreviewResolverTests(unittest.TestCase):
    """试听接口只能读 catalog 里已授权的曲目，不能变成任意文件读取。"""

    def _catalog(self, root: Path, approved: bool = True) -> Path:
        (root / "keep.mp3").write_bytes(b"x")
        (root / "secret.txt").write_bytes(b"secret")
        catalog = root / "catalog.json"
        catalog.write_text(json.dumps({
            "tracks": [{
                "id": "keep", "file": "keep.mp3", "moods": ["舒缓"],
                "styles": ["诗意纪实"], "approved": approved,
            }]
        }, ensure_ascii=False), encoding="utf-8")
        return catalog

    def test_resolves_approved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root)
            self.assertEqual(
                resolve_music_track("keep.mp3", catalog, root),
                (root / "keep.mp3").resolve(),
            )

    def test_rejects_unapproved_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root, approved=False)
            self.assertIsNone(resolve_music_track("keep.mp3", catalog, root))

    def test_rejects_files_not_in_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = self._catalog(root)
            self.assertIsNone(resolve_music_track("secret.txt", catalog, root))
            self.assertIsNone(
                resolve_music_track("../../.env", catalog, root),
                "路径穿越必须被挡掉，即使目标文件存在",
            )


if __name__ == "__main__":
    unittest.main()
