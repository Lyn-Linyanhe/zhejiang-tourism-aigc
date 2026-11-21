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
import time
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


class PexelsAssetProvider(AssetProvider):
    name = "pexels"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.pexels.com/v1",
        video_base_url: str = "https://api.pexels.com/videos",
        media_mode: str = "video-first",
    ) -> None:
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.video_base_url = video_base_url.rstrip("/")
        self.media_mode = media_mode if media_mode in {"video-first", "video-only", "photo-only"} else "video-first"

    @staticmethod
    def _query(shot: dict[str, Any]) -> str:
        query = " ".join(
            str(shot.get("search_query") or shot.get("visual_prompt") or shot.get("title") or "Zhejiang tourism")
            .split()
        )[:180]
        # Knowledge queries may contain a Chinese label followed by English
        # synonyms. Pexels accepts a concise English phrase more reliably.
        alternatives = [part.strip() for part in query.split(";")]
        return next((part for part in alternatives if part.isascii() and any(char.isalpha() for char in part)), alternatives[0])

    @staticmethod
    def _orientation(style_ctx: dict[str, Any] | None) -> str:
        output = (style_ctx or {}).get("output_resolution", "720x1280")
        try:
            width, height = (int(part) for part in str(output).split("x", 1))
        except (ValueError, TypeError):
            width, height = 720, 1280
        return "landscape" if width > height else "square" if width == height else "portrait"

    def _search(self, endpoint: str, query: str, orientation: str, page: int) -> dict[str, Any]:
        params = urllib.parse.urlencode(
            {"query": query, "orientation": orientation, "per_page": 5, "page": page}
        )
        request = urllib.request.Request(
            f"{endpoint}?{params}",
            headers={"Authorization": self.api_key, "User-Agent": "ZhejiangTourismAIGC/2.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Pexels search failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Pexels search returned a non-object payload")
        return payload

    @staticmethod
    def _video_file(video: dict[str, Any], orientation: str, target: tuple[int, int]) -> dict[str, Any] | None:
        files = [item for item in video.get("video_files") or [] if isinstance(item, dict)]
        files = [
            item for item in files
            if str(item.get("link") or "").strip()
            and (
                str(item.get("file_type") or "").lower() in {"", "video/mp4"}
                or str(item.get("link") or "").lower().split("?")[0].endswith(".mp4")
            )
        ]
        target_width, target_height = target
        horizontal = orientation == "landscape"

        def score(item: dict[str, Any]) -> float:
            try:
                width = max(1, int(item.get("width") or 0))
                height = max(1, int(item.get("height") or 0))
            except (TypeError, ValueError):
                return float("inf")
            wrong_orientation = (width >= height) != horizontal if orientation != "square" else abs(width - height) > max(width, height) * 0.15
            undersized = width < target_width * 0.6 or height < target_height * 0.6
            ratio_error = abs(math.log(width / height) - math.log(target_width / target_height))
            size_error = abs(math.log(width / target_width)) + abs(math.log(height / target_height))
            quality_bonus = -0.35 if str(item.get("quality") or "").lower() == "hd" else 0.0
            return (3.0 if wrong_orientation else 0.0) + (2.0 if undersized else 0.0) + ratio_error * 2.0 + size_error + quality_bonus

        return min(files, key=score) if files else None

    def _download(self, url: str, output: Path, kind: str) -> None:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"},
        )
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = response.read()
                if kind == "video" and b"ftyp" not in body[:64] and not body.startswith(b"\x1aE\xdf\xa3"):
                    raise RuntimeError("Pexels video payload is not MP4/WebM")
                if kind == "image" and not body.startswith((b"\xff\xd8\xff", b"\x89PNG", b"RIFF")):
                    raise RuntimeError("Pexels image payload is not a supported raster image")
                output.write_bytes(body)
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f"Pexels {kind} download failed: {exc}") from exc
                time.sleep(2 * (attempt + 1))
        if output.stat().st_size < 1000:
            raise RuntimeError(f"Pexels returned an empty {kind}")

    def _find_video(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        query: str,
        orientation: str,
        page: int,
        target: tuple[int, int],
    ) -> dict[str, Any]:
        endpoint = f"{self.video_base_url}/search"
        payload = self._search(endpoint, query, orientation, page)
        videos = [item for item in payload.get("videos") or [] if isinstance(item, dict)]
        if not videos:
            raise RuntimeError("Pexels returned no video")
        for video in videos:
            selected = self._video_file(video, orientation, target)
            if selected is None:
                continue
            video_path = output_dir / f"{shot['id']}.mp4"
            self._download(str(selected["link"]), video_path, "video")
            user = video.get("user") if isinstance(video.get("user"), dict) else {}
            return {
                "id": f"pexels_video_{video.get('id', shot['id'])}",
                "name": f"Pexels video {video.get('id', shot['id'])}",
                "url": f"/media/{task_id}/assets/{video_path.name}",
                "local_path": str(video_path),
                "source": self.name,
                "license": "Pexels license; verify current terms before publication",
                "author": str(user.get("name") or "Pexels contributor"),
                "usage_scope": "Subject to the Pexels license and team review",
                "is_ai_generated": False,
                "media_type": "video",
                "model": "pexels-video-search",
                "prompt": query,
                "provider_metadata": {
                    "pexels_id": video.get("id"),
                    "pexels_url": video.get("url", ""),
                    "width": selected.get("width"),
                    "height": selected.get("height"),
                    "quality": selected.get("quality", ""),
                },
                "event": {"type": "asset", "provider": self.name, "shot_id": shot.get("id"), "media_type": "video"},
            }
        raise RuntimeError("Pexels videos had no downloadable file")

    def _find_photo(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        query: str,
        orientation: str,
        page: int,
    ) -> dict[str, Any]:
        payload = self._search(f"{self.base_url}/search", query, orientation, page)
        photos = [item for item in payload.get("photos") or [] if isinstance(item, dict)]
        if not photos:
            raise RuntimeError("Pexels returned no photo")
        photo = photos[0]
        image_url = str((photo.get("src") or {}).get("large2x" if orientation == "landscape" else "square" if orientation == "square" else "portrait") or "")
        if not image_url:
            raise RuntimeError(f"Pexels response did not contain a {orientation} image URL")
        image_path = output_dir / f"{shot['id']}.jpg"
        self._download(image_url, image_path, "image")
        return {
            "id": f"pexels_photo_{photo.get('id', shot['id'])}",
            "name": f"Pexels photo {photo.get('id', shot['id'])}",
            "url": f"/media/{task_id}/assets/{image_path.name}",
            "local_path": str(image_path),
            "source": self.name,
            "license": "Pexels license; verify current terms before publication",
            "author": str(photo.get("photographer") or "Pexels contributor"),
            "usage_scope": "Subject to the Pexels license and team review",
            "is_ai_generated": False,
            "media_type": "image",
            "model": "pexels-photo-search",
            "prompt": query,
            "provider_metadata": {"pexels_id": photo.get("id"), "pexels_url": photo.get("url", "")},
            "event": {"type": "asset", "provider": self.name, "shot_id": shot.get("id"), "media_type": "image"},
        }

    def create_or_find(
        self,
        task_id: str,
        shot: dict[str, Any],
        output_dir: Path,
        theme_color: str,
        accent: str,
        style_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("PEXELS_API_KEY is required when asset_source=pexels")
        output_dir.mkdir(parents=True, exist_ok=True)
        query = self._query(shot)
        orientation = self._orientation(style_ctx)
        output = (style_ctx or {}).get("output_resolution", "720x1280")
        try:
            target = tuple(int(part) for part in str(output).split("x", 1))
        except (ValueError, TypeError):
            target = (720, 1280)
        if len(target) != 2 or min(target) <= 0:
            target = (720, 1280)
        page = max(1, min(80, int(shot.get("order", 1) or 1)))
        mode = str((style_ctx or {}).get("pexels_media_mode") or self.media_mode).lower()
        failures: list[str] = []
        if mode != "photo-only":
            try:
                return self._find_video(task_id, shot, output_dir, query, orientation, page, target)
            except Exception as exc:
                failures.append(f"video: {exc}")
                if mode == "video-only":
                    raise RuntimeError("; ".join(failures)) from exc
        if mode != "video-only":
            try:
                return self._find_photo(task_id, shot, output_dir, query, orientation, page)
            except Exception as exc:
                failures.append(f"photo: {exc}")
        raise RuntimeError(f"Pexels returned no usable media for query {query!r}: {'; '.join(failures)}")


class LocalUploadProvider:
    name = "local-upload"

    @staticmethod
    def save_base64(data: str, output_path: Path) -> None:
        payload = data.split(",", 1)[1] if "," in data else data
        output_path.write_bytes(base64.b64decode(payload))
