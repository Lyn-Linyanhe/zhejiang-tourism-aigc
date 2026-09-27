"""FFmpeg 与字幕工具。

从原 media.py 拆出，归属渲染线（P4）。改渲染实现、调色、转场、字幕时只动本文件，
不会和声音线的改动撞车。

    run_ffmpeg          统一的 ffmpeg 调用入口
    generate_tone       合成音调（音频降级兜底用）
    validate_audio_file 音频文件可解码性校验
    create_srt          优先按配音 cues 生成字幕，没有 cues 才按镜头时长切分
    shot_filter         静图运镜、视频铺时长、统一调色
    transition_filter   段内转场（不跨段重叠，不缩短总时长）
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


# 与 contract.TRANSITIONS 对齐。实现全部是段内效果，禁止 xfade 跨段重叠。
TRANSITIONS = ("fade", "dissolve", "slideleft", "slideright", "zoom")

# 与 consistency.DEFAULT_GRADE 一致，再补上分工文档里的 curves / colorbalance。
# 调用方没传 grade 时用这一套，数值保持温和，避免六镜之间色调跳。
DEFAULT_GRADE = {
    "saturation": 1.06,
    "contrast": 1.04,
    "brightness": 0.01,
    "curves": "medium_contrast",
    "colorbalance_rm": 0.01,
    "colorbalance_bm": 0.02,
}

# 段内淡入淡出上限。再长会把短镜头中间也吃掉，看起来不像转场。
MAX_TRANSITION_SEC = 0.45


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


def srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _cue_value(raw: Any, key: str, default: Any = "") -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _usable_cues(cues: list[Any] | None) -> list[dict[str, Any]]:
    usable: list[dict[str, Any]] = []
    if not cues:
        return usable
    for raw in cues:
        try:
            start = float(_cue_value(raw, "start", 0))
            end = float(_cue_value(raw, "end", 0))
        except (TypeError, ValueError):
            continue
        text = str(_cue_value(raw, "text", "") or _cue_value(raw, "subtitle", "")).strip()
        if end <= start or not text:
            continue
        usable.append(
            {
                "shot_id": str(_cue_value(raw, "shot_id", "") or ""),
                "start": start,
                "end": end,
                "text": text,
            }
        )
    usable.sort(key=lambda item: (item["start"], item["end"]))
    return usable


def create_srt(
    shots: list[dict[str, Any]],
    output_path: Path,
    cues: list[dict[str, Any]] | None = None,
    picture_end: float | None = None,
) -> None:
    """生成字幕。有配音 cues 时用真实起止时间，否则按镜头名义时长切分。

    cues 来自 audio["cues"]，字段与 contract.Cue 一致：shot_id / start / end / text。
    没有 cues、cues 为空、或条目缺时间/文本时，退回旧的按镜头时长切分，
    这样调用方暂时不传 cues 时字幕仍能生成。
    picture_end 是画面结束时间。字幕可以在画面内提前结束，但不能写到画面之后。
    """
    lines: list[str] = []
    timed = _usable_cues(cues)
    if picture_end is not None and picture_end > 0:
        clipped: list[dict[str, Any]] = []
        for cue in timed:
            start = min(float(cue["start"]), picture_end)
            end = min(float(cue["end"]), picture_end)
            # 贴着画面结尾时仍留一个可见窗口，避免最后一句被滤成空。
            if end <= start:
                start = max(0.0, end - 0.05)
            if end <= start:
                continue
            clipped.append({**cue, "start": start, "end": end})
        timed = clipped
    if timed:
        for index, cue in enumerate(timed, start=1):
            lines.extend(
                [
                    str(index),
                    f"{srt_timestamp(cue['start'])} --> {srt_timestamp(cue['end'])}",
                    cue["text"],
                    "",
                ]
            )
    else:
        elapsed = 0.0
        for index, shot in enumerate(shots, start=1):
            start = elapsed
            elapsed += float(shot["duration_sec"])
            lines.extend(
                [
                    str(index),
                    f"{srt_timestamp(start)} --> {srt_timestamp(elapsed)}",
                    shot["subtitle"],
                    "",
                ]
            )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def _grade_number(grade: dict[str, Any] | None, key: str, default: float) -> float:
    if not grade or key not in grade or grade[key] in (None, ""):
        return default
    try:
        return float(grade[key])
    except (TypeError, ValueError):
        return default


def grade_filter(grade: dict[str, Any] | None = None) -> str:
    """统一调色。传入的 grade 覆盖同名字段，缺的字段用温和默认值。"""
    saturation = _grade_number(grade, "saturation", DEFAULT_GRADE["saturation"])
    contrast = _grade_number(grade, "contrast", DEFAULT_GRADE["contrast"])
    brightness = _grade_number(grade, "brightness", DEFAULT_GRADE["brightness"])
    curves = DEFAULT_GRADE["curves"]
    if grade and grade.get("curves") not in (None, ""):
        curves = str(grade["curves"]).strip() or curves
    # 只接受 ffmpeg curves preset 名，避免把任意字符串拼进滤镜。
    if curves not in {"none", "color_negative", "cross_process", "darker", "increase_contrast", "lighter", "linear_contrast", "medium_contrast", "negative", "strong_contrast", "vintage"}:
        curves = str(DEFAULT_GRADE["curves"])
    rm = _grade_number(
        grade,
        "colorbalance_rm",
        _grade_number(grade, "rm", DEFAULT_GRADE["colorbalance_rm"]),
    )
    bm = _grade_number(
        grade,
        "colorbalance_bm",
        _grade_number(grade, "bm", DEFAULT_GRADE["colorbalance_bm"]),
    )
    parts = [
        f"eq=saturation={saturation:.4f}:contrast={contrast:.4f}:brightness={brightness:.4f}",
    ]
    if curves != "none":
        parts.append(f"curves=preset={curves}")
    parts.append(f"colorbalance=rm={rm:.4f}:bm={bm:.4f}")
    return ",".join(parts)


def is_video_media(media_type: str, path: Path) -> bool:
    """视频素材不再套运镜。优先看 asset_media_type，缺省再按扩展名。"""
    kind = str(media_type or "").strip().lower()
    if kind == "video":
        return True
    if kind == "image":
        return False
    return path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def shot_filter(
    width: int,
    height: int,
    duration: float,
    fps: int,
    *,
    video: bool,
    grade: dict[str, Any] | None = None,
) -> str:
    """静图先放大再缓慢推近；视频只铺到画布并调色，不再 zoompan。

    zoompan 直接在原分辨率上推会抖。先放大到 2 倍再 crop，输出仍是目标分辨率。
    推近速率沿用分工文档的 0.0008，上限 1.15，避免短镜头一下子推满。
    """
    # 多生成一帧，交给输出端的 -t 裁到镜头时长。zoompan 的 d 若偏短，
    # 这一段会比 duration_sec 短，六段加起来就不再等于总时长。
    frames = max(2, int(duration * fps + 0.999) + 1)
    color = grade_filter(grade)
    if video:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"{color},"
            f"fps={fps}"
        )
    return (
        f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
        f"crop={width * 2}:{height * 2},"
        f"zoompan=z='min(zoom+0.0008,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
        f"{color}"
    )


def transition_window(duration: float) -> float:
    """段内淡入淡出时长。短镜头按比例缩短，保证中间仍有完整画面。"""
    if duration <= 0.4:
        return 0.0
    return min(MAX_TRANSITION_SEC, duration * 0.18)


def _fade_pair(duration: float, window: float, color: str) -> str:
    fade_out_start = max(0.0, duration - window)
    return (
        f"fade=t=in:st=0:d={window:.3f}:color={color},"
        f"fade=t=out:st={fade_out_start:.3f}:d={window:.3f}:color={color}"
    )


def transition_filter(
    name: str,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> str:
    """五个声明过的转场，全部做成段内效果，不和邻段重叠。

    fade：段首段尾黑场淡入淡出。
    dissolve：同样不跨段，但淡到白，时长略长，和 fade 能看出来不是同一种。
    slideleft / slideright：先把画面垫宽，再裁一个移动窗口，从一侧滑到居中，
    同时淡入淡出。不能在刚好等于输出尺寸的画面上裁，否则 x 会被夹到 0，滑不动。
    zoom：用 scale 的逐帧表达式推近再裁回原尺寸。不能再套一次 zoompan——
    zoompan 会按输入帧数乘 d，把这一段的时长拉长。
    未知名字退回 fade。fps 保留给调用方，缩放本身不靠它计帧。
    """
    del fps  # 段内转场按秒计算，避免再引入按帧放大时长的滤镜。
    kind = str(name or "fade").strip().lower()
    if kind not in TRANSITIONS:
        kind = "fade"
    window = transition_window(duration)
    if window <= 0:
        return ""
    if kind == "dissolve":
        longer = min(max(window * 1.35, window + 0.05), duration * 0.30)
        return _fade_pair(duration, longer, "white")
    fade = _fade_pair(duration, window, "black")
    if kind in {"slideleft", "slideright"}:
        # 画面先加宽，左右才有余量。滑动覆盖前 70% 的镜头，淡入只占开头一小段，
        # 否则运动会在黑场里结束，看起来和 fade 没有区别。
        travel = max(80, (width // 3) // 2 * 2)
        slide = min(duration * 0.7, max(window, 0.8))
        if kind == "slideleft":
            x_expr = f"{travel}*(1-min(1,t/{slide:.3f}))"
        else:
            x_expr = f"{travel}*min(1,t/{slide:.3f})"
        return (
            f"scale={width + travel}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width + travel}:{height},"
            f"crop={width}:{height}:x='{x_expr}':y=0,"
            f"{fade}"
        )
    if kind == "zoom":
        # 整段持续推近，不在淡入窗口结束时停住。scale 按帧求值，不会把时长拉长。
        grown = f"1+0.22*min(1,t/{max(duration, 0.1):.3f})"
        return (
            "scale="
            f"w='trunc({width}*({grown})/2)*2':"
            f"h='trunc({height}*({grown})/2)*2':"
            "eval=frame,"
            f"crop={width}:{height}:(in_w-{width})/2:(in_h-{height})/2,"
            f"{fade}"
        )
    return fade
