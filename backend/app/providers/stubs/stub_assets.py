"""画面 stub —— P2 的替身，第一天就能让 P1 跑通全链路。

产出的 PPM 图是纯 Python 画的程序化场景（山、水、日晕），
丑但能跑，不需要任何外部依赖。

用法（独立验证，不依赖项目其他部分）：
    python stub_assets.py --out-dir /tmp/fake --shot-count 6
    python stub_assets.py --video --out-dir /tmp/fake    # 出 mp4 而非静图

P1 接入方式：把它当成一个 AssetProvider 注册进 Registry，
云 provider 全部失败时降级到它。
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

# 让本文件既能被项目 import，也能单独跑
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AssetResult, MEDIA_TYPE_IMAGE, MEDIA_TYPE_VIDEO  # noqa: E402


DEFAULT_PALETTE = {
    "base": "#24526a",
    "accent": "#d6ad60",
}


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        return (40, 70, 78)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _draw_scene(image_path: Path, order: int, width: int, height: int, palette: dict) -> None:
    """画一张程序化场景图。纯 Python 写 PPM，无第三方依赖。"""
    base = _hex_rgb(palette.get("base", DEFAULT_PALETTE["base"]))
    glow = _hex_rgb(palette.get("accent", DEFAULT_PALETTE["accent"]))
    image_path.parent.mkdir(parents=True, exist_ok=True)
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
                    mix = (y - mountain) / max(1, horizon - mountain)
                    red = int(base[0] * (0.28 + mix * 0.18) + glow[0] * 0.12)
                    green = int(base[1] * (0.35 + mix * 0.20) + glow[1] * 0.16)
                    blue = int(base[2] * (0.42 + mix * 0.18) + glow[2] * 0.10)
                else:
                    mix = (y - horizon) / max(1, height - horizon)
                    ripple = 10 if ((y + x * 3 + order * 17) % 29) < 3 else 0
                    red = int(base[0] * (0.20 + mix * 0.12) + ripple)
                    green = int(base[1] * (0.36 + mix * 0.16) + ripple)
                    blue = int(base[2] * (0.48 + mix * 0.18) + ripple)
                if y > int(height * 0.74) and (x // 28 + order) % 3 == 0:
                    building_height = 35 + ((x * 7 + order * 13) % 90)
                    if y > height - building_height:
                        red, green, blue = 18, 35, 39
                if sun_distance < width * 0.12:
                    red = min(255, red + 70)
                    green = min(255, green + 46)
                    blue = min(255, blue + 12)
                file.write(
                    bytes(
                        (
                            max(0, min(255, red)),
                            max(0, min(255, green)),
                            max(0, min(255, blue)),
                        )
                    )
                )


def _image_to_video(image_path: Path, video_path: Path, duration: float, fps: int) -> None:
    """用 ffmpeg 把静图铺成一段视频，模拟图生视频的产物。"""
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(image_path),
            "-t", f"{duration:.2f}",
            "-vf", "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p",
            "-r", str(fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "28",
            "-an", str(video_path),
        ],
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "stub 视频生成失败")


class StubAssetProvider:
    """与 contract.AssetProviderProtocol 形状一致，可直接注册进 Registry。"""

    name = "stub-asset"
    cost_per_call = 0.0

    def __init__(
        self,
        out_dir: Path,
        as_video: bool = False,
        duration: float = 5.0,
        fps: int = 25,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.as_video = as_video
        self.duration = duration
        self.fps = fps

    def generate(
        self,
        shot: dict[str, Any],
        output_dir: Any = None,
        style_ctx: dict[str, Any] | None = None,
    ) -> AssetResult:
        target = Path(output_dir) if output_dir else self.out_dir
        target.mkdir(parents=True, exist_ok=True)
        shot_id = str(shot.get("id") or f"shot_{shot.get('order', 1)}")
        order = int(shot.get("order", 1))
        style_ctx = style_ctx or {}
        palette = {
            "base": style_ctx.get("color", DEFAULT_PALETTE["base"]),
            "accent": style_ctx.get("accent", DEFAULT_PALETTE["accent"]),
        }

        image_path = target / f"{shot_id}.ppm"
        _draw_scene(image_path, order, 360, 640, palette)

        if not self.as_video:
            result = AssetResult(
                path=str(image_path),
                media_type=MEDIA_TYPE_IMAGE,
                source=self.name,
                model="procedural-scene-v1",
                seed=1000 + order,
                prompt=str(shot.get("visual_prompt", "")),
            )
            result.validate()
            return result

        video_path = target / f"{shot_id}.mp4"
        _image_to_video(image_path, video_path, self.duration, self.fps)
        result = AssetResult(
            path=str(video_path),
            media_type=MEDIA_TYPE_VIDEO,
            source=self.name,
            model="procedural-scene-v1",
            seed=1000 + order,
            prompt=str(shot.get("visual_prompt", "")),
            first_frame_path=str(image_path),
        )
        result.validate()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 stub 画面素材")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--shot-count", type=int, default=6)
    parser.add_argument("--video", action="store_true", help="产出 mp4 而非静图")
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()

    provider = StubAssetProvider(
        out_dir=Path(args.out_dir),
        as_video=args.video,
        duration=args.duration,
    )
    for index in range(1, args.shot_count + 1):
        result = provider.generate(
            {"id": f"shot_{index}", "order": index, "visual_prompt": f"测试镜头 {index}"}
        )
        print(f"[ok] {result.media_type:5s} {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
