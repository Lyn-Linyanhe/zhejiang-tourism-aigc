"""FFmpeg 与字幕工具。

从原 media.py 拆出，归属渲染线（P4）。改渲染实现、调色、转场、字幕时只动本文件，
不会和声音线的改动撞车。

    run_ffmpeg          统一的 ffmpeg 调用入口
    generate_tone       合成音调（音频降级兜底用）
    validate_audio_file 音频文件可解码性校验
    create_srt          按镜头时长生成字幕
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def generate_tone(output_path: Path, duration: float, frequency: int = 440, volume: float = 0.18) -> dict[str, Any]:
    result = run_ffmpeg(
        [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=44100",
            "-t",
            f"{max(0.2, duration):.2f}",
            "-filter:a",
            f"volume={volume}",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg 音频生成失败")
    return {"path": str(output_path), "duration_sec": duration}


def validate_audio_file(path: Path) -> None:
    if not path.exists() or path.stat().st_size < 1000:
        raise RuntimeError("Uploaded audio file is empty")
    result = run_ffmpeg(
        ["-nostdin", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Audio file is not decodable")


def create_srt(shots: list[dict[str, Any]], output_path: Path) -> None:
    """按镜头名义时长生成字幕。

    注意：这是「名义时长均分」，不是配音的真实时间位置。有了 TTS 的词级时间戳
    之后，这里应改为消费 cues（见 00_契约/CONTRACT.md 的 AudioResult）。
    """
    elapsed = 0.0
    lines: list[str] = []

    def timestamp(seconds: float) -> str:
        total_ms = int(round(seconds * 1000))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    for index, shot in enumerate(shots, start=1):
        start = elapsed
        elapsed += float(shot["duration_sec"])
        lines.extend([str(index), f"{timestamp(start)} --> {timestamp(elapsed)}", shot["subtitle"], ""])
    output_path.write_text("\n".join(lines), encoding="utf-8")
