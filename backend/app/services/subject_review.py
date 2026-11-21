"""Task-local subject references and visual inspection sheets (stdlib + FFmpeg)."""

import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path


def save_reference(folder, encoded):
    data = base64.b64decode(encoded.split(",")[-1], validate=True)
    if len(data) > 12 * 1024 * 1024 or not data.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
    ):
        raise ValueError("请选择 12 MB 以内的 PNG 或 JPEG 参考图")
    key = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (key + ".jpg")
    if not target.exists():
        frame = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "image2pipe",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=1920:1920:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=30,
        )
        if frame.returncode or not frame.stdout:
            raise ValueError("参考图无法解码")
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(dir=folder, delete=False) as f:
            f.write(frame.stdout)
            temp = Path(f.name)
        temp.replace(target)
    return key


def reference_path(folder, key):
    if not re.fullmatch("[0-9a-f]{64}", key) or not (folder / (key + ".jpg")).is_file():
        raise ValueError("参考图不属于当前任务，请重新上传")
    return str(folder / (key + ".jpg"))


def review_group(name, shots, directory):
    width, height = 224, 144
    pixels = bytearray()
    rows = []
    for shot in shots:
        path = shot.asset_local_path
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height:format=duration",
                    "-of",
                    "json",
                    path,
                ]
            )
        )
        stream = probe["streams"][0]
        duration = float(probe.get("format", {}).get("duration", 0) or 0)
        frames = []
        samples = []
        for at in (0, duration * 0.5, max(0, duration - 0.15)):
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(round(at, 4)),
                "-i",
                path,
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "pipe:1",
            ]
            frame = subprocess.check_output(cmd, timeout=30)
            if len(frame) != width * height * 3:
                raise ValueError("抽帧不完整")
            frames.append(frame)
            samples.append(
                {
                    "second": round(at, 3),
                    "near_black": sum(v < 18 for v in frame) / len(frame) > 0.97,
                }
            )
        for y in range(height):
            for frame in frames:
                pixels.extend(frame[y * width * 3 : (y + 1) * width * 3])
        rows.append(
            dict(
                shot_id=shot.id,
                width=stream["width"],
                height=stream["height"],
                samples=samples,
            )
        )
    filename = "subject-" + hashlib.sha256(name.encode()).hexdigest()[:16] + ".jpg"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            f"{width * 3}x{height * len(shots)}",
            "-i",
            "pipe:0",
            "-frames:v",
            "1",
            str(directory / filename),
        ],
        input=pixels,
        check=True,
        timeout=30,
    )
    return dict(group=name, file=filename, rows=rows, columns=["首帧", "中帧", "尾帧"])
