"""素材 provider —— 只负责「一个镜头拿到一张图或一段视频」。

原 media.py 曾同时装着素材、FFmpeg 工具和 TTS 实现，三类东西的维护者不同，
会让两个人反复改同一个文件。已拆分为：

    media.py          素材 provider（本文件）
    media_ffmpeg.py   FFmpeg 与字幕工具
    tts.py            TTS 实现（函数式 + 类式）
    music.py          音乐匹配

新增素材来源时只改本文件。
"""

from __future__ import annotations

import base64
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class AssetProvider:
    name = "base"

    def create_or_find(self, task_id: str, shot: dict[str, Any], output_dir: Path, theme_color: str, accent: str) -> dict[str, Any]:
        raise NotImplementedError


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (40, 70, 78)
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


class OfflineAssetProvider(AssetProvider):
    name = "offline-scene-generator"

    def create_or_find(self, task_id: str, shot: dict[str, Any], output_dir: Path, theme_color: str, accent: str) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / f"{shot['id']}.ppm"
        width, height = 360, 640
        base = _hex_rgb(theme_color)
        glow = _hex_rgb(accent)
        order = int(shot["order"])
        with image_path.open("wb") as file:
            file.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
            for y in range(height):
                for x in range(width):
                    horizon = int(height * 0.58 + math.sin((x + order * 19) / 37) * 26)
                    mountain = int(height * 0.43 + math.sin((x + order * 31) / 48) * 28)
                    sun_distance = ((x - width * 0.72) ** 2 + (y - height * 0.22) ** 2) ** 0.5
                    sun = max(0.0, 1.0 - sun_distance / (width * 0.24))
                    if y < mountain:
                        sky_mix = y / max(1, mountain)
                        red = int(base[0] * (0.72 + sky_mix * 0.18) + glow[0] * sun * 0.55)
                        green = int(base[1] * (0.72 + sky_mix * 0.16) + glow[1] * sun * 0.42)
                        blue = int(base[2] * (0.82 + sky_mix * 0.10) + glow[2] * sun * 0.20)
                    elif y < horizon:
                        mountain_mix = (y - mountain) / max(1, horizon - mountain)
                        red = int(base[0] * (0.28 + mountain_mix * 0.18) + glow[0] * 0.12)
                        green = int(base[1] * (0.35 + mountain_mix * 0.20) + glow[1] * 0.16)
                        blue = int(base[2] * (0.42 + mountain_mix * 0.18) + glow[2] * 0.10)
                    else:
                        water_mix = (y - horizon) / max(1, height - horizon)
                        ripple = 10 if ((y + x * 3 + order * 17) % 29) < 3 else 0
                        red = int(base[0] * (0.20 + water_mix * 0.12) + ripple)
                        green = int(base[1] * (0.36 + water_mix * 0.16) + ripple)
                        blue = int(base[2] * (0.48 + water_mix * 0.18) + ripple)
                    if y > int(height * 0.74) and (x // 28 + order) % 3 == 0:
                        building_height = 35 + ((x * 7 + order * 13) % 90)
                        if y > height - building_height:
                            red, green, blue = 18, 35, 39
                    if sun_distance < width * 0.12:
                        red = min(255, red + 70)
                        green = min(255, green + 46)
                        blue = min(255, blue + 12)
                    red = max(0, min(255, red))
                    green = max(0, min(255, green))
                    blue = max(0, min(255, blue))
                    file.write(bytes((red, green, blue)))
        return {
            "id": f"asset_{shot['id']}",
            "name": f"离线场景图-{shot['title']}",
            "url": f"/media/{task_id}/assets/{image_path.name}",
            "local_path": str(image_path),
            "source": self.name,
            "license": "本地离线生成场景图；公开发布前需人工审核",
            "author": "system",
            "usage_scope": "演示、内部评审、待授权替换",
            "is_ai_generated": True,
        }


# 验收和演示用的西湖镜头。检索词必须落到这个景点，不能拿「杭州风景」碰运气。
# 值是候选词列表：前一个对不上说明文字时，换下一个。
LANDMARK_QUERIES = {
    "苏堤": ["Su Causeway West Lake Hangzhou willow", "West Lake Hangzhou willow causeway"],
    "断桥": ["Hangzhou traditional stone bridge tourists boat", "stone arch bridge Hangzhou lake"],
    "白堤": ["West Lake Hangzhou autumn trees lake road", "Hangzhou lake road trees"],
    "三潭印月": ["Three Pools Mirroring the Moon West Lake", "West Lake stone pagoda lantern water"],
    "雷峰": ["Leifeng Pagoda West Lake Hangzhou", "Hangzhou pagoda West Lake"],
    "平湖秋月": ["West Lake Hangzhou pavilion lotus", "Hangzhou pavilion lake lotus"],
    "西湖": ["West Lake Hangzhou"],
}
# 说明文字里至少要有其中一个词，否则这张图不算这个景点。
LANDMARK_NEEDLES = {
    "苏堤": ("causeway", "willow", "su "),
    "断桥": ("broken", "arch", "bridge", "snow"),
    "白堤": ("causeway", "bai", "plane"),
    "三潭印月": ("pool", "lantern", "pagoda", "moon"),
    "雷峰": ("leifeng", "pagoda"),
    "平湖秋月": ("pavilion", "lotus"),
    "西湖": ("west lake", "hangzhou"),
}

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def landmark_queries(shot: dict[str, Any]) -> tuple[str, list[str]]:
    """按镜头标题和旁白选景点，返回景点名和候选检索词。"""
    text = " ".join(
        str(shot.get(key) or "")
        for key in ("title", "subtitle", "narration", "visual_prompt")
    )
    for name, queries in LANDMARK_QUERIES.items():
        if name in text:
            return name, list(queries)
    precise = str(shot.get("search_query") or "").strip()
    if precise:
        return "", [precise[:180]]
    return "西湖", list(LANDMARK_QUERIES["西湖"])


def pick_pexels_video(
    videos: list[dict[str, Any]],
    needed_sec: float,
    used_ids: set[str],
) -> dict[str, Any] | None:
    """选一条够长、还没用过的实拍。静图推镜头不像剪过，能用视频就不用图。"""
    candidates = []
    for video in videos:
        video_id = str(video.get("id") or "")
        if not video_id or video_id in used_ids:
            continue
        try:
            duration = float(video.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration + 0.05 < needed_sec:
            continue
        files = [
            item
            for item in (video.get("video_files") or [])
            if str(item.get("file_type") or "").lower() == "video/mp4" and item.get("link")
        ]
        if not files:
            continue
        file = min(files, key=lambda item: abs(int(item.get("height") or 0) - 720))
        width = int(file.get("width") or 0)
        height = int(file.get("height") or 0)
        landscape = 1 if width >= height else 0
        candidates.append((landscape, -abs(height - 720), video, file))
    if not candidates:
        return None
    _landscape, _height, video, file = max(candidates, key=lambda item: item[:2])
    return {"video": video, "file": file}


def pick_pexels_photo(
    photos: list[dict[str, Any]],
    query: str,
    needles: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """不要永远拿第一张。说明文字对得上景点词的优先；都对不上时用竖图里最像的一张。"""
    if not photos:
        return None
    tokens = {token.lower() for token in query.split() if len(token) > 2}
    ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for photo in photos:
        blob = " ".join(str(photo.get(key) or "") for key in ("alt", "url")).lower()
        needle_hits = sum(1 for needle in needles if needle in blob)
        hits = sum(1 for token in tokens if token in blob)
        height = int(photo.get("height") or 0)
        width = int(photo.get("width") or 0)
        portrait = 1 if height >= width else 0
        ranked.append(((needle_hits, portrait, hits), photo))
    matched = [item for item in ranked if item[0][0] > 0] if needles else ranked
    pool = matched or ranked
    return max(pool, key=lambda item: item[0])[1]


class PexelsAssetProvider(AssetProvider):
    name = "pexels"

    def __init__(self, api_key: str, base_url: str = "https://api.pexels.com/v1") -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self._used_video_ids: set[str] = set()

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("PEXELS_API_KEY is required when asset_source=pexels")
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            needed = float(shot.get("duration_sec") or 0)
        except (TypeError, ValueError):
            needed = 0.0
        if needed <= 12:
            video_asset = self._try_video(task_id, shot, output_dir, needed or 4.0)
            if video_asset is not None:
                return video_asset
        landmark, queries = landmark_queries(shot)
        needles = LANDMARK_NEEDLES.get(landmark, ())
        photo: dict[str, Any] | None = None
        query = queries[0]
        for query in queries:
            params = urllib.parse.urlencode(
                {"query": query, "orientation": "portrait", "per_page": 15}
            )
            request = urllib.request.Request(
                f"{self.base_url}/search?{params}",
                headers={
                    "Authorization": self.api_key,
                    # Pexels 的 Cloudflare 会拦截 Python 默认 UA（error 1010），必须带浏览器 UA
                    "User-Agent": _BROWSER_UA,
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Pexels search failed: {exc}") from exc
            photo = pick_pexels_photo(payload.get("photos") or [], query, needles)
            if photo is not None:
                break
        if photo is None:
            raise RuntimeError(f"Pexels returned no matching photo for {landmark or query}")

    def _try_video(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        needed_sec: float,
    ) -> dict[str, Any] | None:
        """西湖实拍优先。搜不到够长的片段时返回 None，调用方再退回照片。"""
        _landmark, queries = landmark_queries(shot)
        query = queries[0] if queries else "West Lake Hangzhou"
        params = urllib.parse.urlencode(
            {"query": query, "orientation": "landscape", "per_page": 8, "size": "medium"}
        )
        video_root = self.base_url.replace("/v1", "") + "/videos"
        request = urllib.request.Request(
            f"{video_root}/search?{params}",
            headers={"Authorization": self.api_key, "User-Agent": _BROWSER_UA},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return None
        picked = pick_pexels_video(payload.get("videos") or [], needed_sec, self._used_video_ids)
        if picked is None:
            return None
        video = picked["video"]
        file = picked["file"]
        video_id = str(video.get("id"))
        path = output_dir / f"{shot['id']}.mp4"
        try:
            download = urllib.request.Request(
                str(file["link"]),
                headers={"User-Agent": _BROWSER_UA},
            )
            with urllib.request.urlopen(download, timeout=90) as response:
                path.write_bytes(response.read())
        except Exception:
            return None
        if path.stat().st_size < 10_000:
            return None
        self._used_video_ids.add(video_id)
        try:
            duration = float(video.get("duration") or needed_sec)
        except (TypeError, ValueError):
            duration = needed_sec
        # 从中段起用，避开片头标题和片尾黑场。
        start = max(0.0, (duration - needed_sec) / 2)
        return {
            "id": f"pexels_video_{video_id}",
            "name": f"Pexels video {video_id}",
            "url": f"/media/{task_id}/assets/{path.name}",
            "local_path": str(path),
            "source": self.name,
            "license": "Pexels license; verify current terms before publication",
            "author": str(((video.get("user") or {}).get("name")) or "Pexels contributor"),
            "usage_scope": "Subject to the Pexels license and team review",
            "is_ai_generated": False,
            "media_type": "video",
            "clip_start": round(start, 3),
        }
        image_url = str((photo.get("src") or {}).get("portrait") or "")
        if not image_url:
            raise RuntimeError("Pexels response did not contain a portrait image URL")
        image_path = output_dir / f"{shot['id']}.jpg"
        try:
            download_request = urllib.request.Request(
                image_url,
                headers={
                    # 图片 CDN 同样受 Cloudflare 防护，必须带浏览器 UA
                    "User-Agent": _BROWSER_UA,
                },
            )
            with urllib.request.urlopen(download_request, timeout=60) as response:
                image_path.write_bytes(response.read())
        except Exception as exc:
            raise RuntimeError(f"Pexels image download failed: {exc}") from exc
        if image_path.stat().st_size < 1000:
            raise RuntimeError("Pexels returned an empty image")
        return {
            "id": f"pexels_{photo.get('id', shot['id'])}",
            "name": f"Pexels photo {photo.get('id', shot['id'])}",
            "url": f"/media/{task_id}/assets/{image_path.name}",
            "local_path": str(image_path),
            "source": self.name,
            "license": "Pexels license; verify current terms before publication",
            "author": str((photo.get("photographer") or "Pexels contributor")),
            "usage_scope": "Subject to the Pexels license and team review",
            "is_ai_generated": False,
        }


class LocalUploadProvider:
    name = "local-upload"

    @staticmethod
    def save_base64(data: str, output_path: Path) -> None:
        payload = data.split(",", 1)[1] if "," in data else data
        output_path.write_bytes(base64.b64decode(payload))
