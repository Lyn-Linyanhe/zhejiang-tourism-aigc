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

# 只在镜头交界处做短切。再长会把画面吃成黑场或白场。
MAX_TRANSITION_SEC = 0.18


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
    """静图按时间连续推近；视频只铺到画布并调色。

    不用 zoompan。它按整像素跳，再碰上循环输入会每帧重置，看起来一卡一卡。
    crop 不认时间变量，所以先放大，再用 scale 的逐帧表达式推近，最后裁回目标尺寸。
    推近上限约 1.12，短镜头不会一下子推满。
    """
    color = grade_filter(grade)
    if video:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"{color},"
            f"fps={fps}"
        )
    span = max(float(duration), 0.1)
    grown = f"1+0.12*min(1\\,t/{span:.3f})"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"scale=w='trunc({width}*({grown})/2)*2':h='trunc({height}*({grown})/2)*2':eval=frame:flags=bicubic,"
        f"crop={width}:{height}:(in_w-{width})/2:(in_h-{height})/2,"
        f"fps={fps},"
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

    只在镜头结尾收一下，开头不再从黑场或白场淡入。否则六段拼起来就是
    反复闪白、闪黑，中间的画面也看不清。
    fade：结尾短黑。dissolve：结尾短白，和 fade 能区分。
    slideleft / slideright：画面垫宽后滑到居中，不再叠黑场。
    zoom：整段轻微推近。不能再套 zoompan，它会按输入帧数乘 d，把时长拉长。
    未知名字退回 fade。fps 保留给调用方，缩放本身不靠它计帧。
    """
    del fps  # 段内转场按秒计算，避免再引入按帧放大时长的滤镜。
    kind = str(name or "fade").strip().lower()
    if kind not in TRANSITIONS:
        kind = "fade"
    window = transition_window(duration)
    if window <= 0:
        return ""
    fade_out_start = max(0.0, duration - window)
    if kind == "dissolve":
        return f"fade=t=out:st={fade_out_start:.3f}:d={window:.3f}:color=white"
    fade = f"fade=t=out:st={fade_out_start:.3f}:d={window:.3f}:color=black"
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
            f"crop={width}:{height}:x='{x_expr}':y=0"
        )
    if kind == "zoom":
        # 整段持续推近，不在淡入窗口结束时停住。scale 按帧求值，不会把时长拉长。
        grown = f"1+0.22*min(1,t/{max(duration, 0.1):.3f})"
        return (
            "scale="
            f"w='trunc({width}*({grown})/2)*2':"
            f"h='trunc({height}*({grown})/2)*2':"
            "eval=frame,"
            f"crop={width}:{height}:(in_w-{width})/2:(in_h-{height})/2"
        )
    return fade
