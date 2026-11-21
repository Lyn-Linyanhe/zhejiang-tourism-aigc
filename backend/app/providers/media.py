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
import math
from .stock import StockProvider
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


class PexelsAssetProvider(StockProvider):
    name = "pexels"

    def __init__(self, api_key: str, base_url: str = "https://api.pexels.com/v1"):
        super().__init__([("pexels", api_key, base_url.rstrip("/"))])


class LocalUploadProvider:
    name = "local-upload"

    @staticmethod
    def save_base64(data: str, output_path: Path) -> None:
        payload = data.split(",", 1)[1] if "," in data else data
        output_path.write_bytes(base64.b64decode(payload))
