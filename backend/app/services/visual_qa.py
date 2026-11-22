"""可复现的视觉效果验收。

这里的指标分成三层，报告中必须分开看：

1. 生成契约：主体描述、参考图 SHA-256、风格前缀、负向词是否逐镜一致。
2. 画面质量：解码后的亮度、对比度、清晰度、熵、死黑/死白比例。
3. 跨镜漂移：亮度/色彩离散度和感知哈希距离。这是“是否明显跑片”的代理指标，
   不是人脸/物体识别；若要求语义级身份核验，必须再接入主体分割或视觉嵌入模型。

所有数值都来自实际像素。没有 FFmpeg 且不是 PPM 时，报告明确写 unverified，
不会把“未检查”记成“通过”。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import consistency


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
QA_VERSION = "visual-acceptance-v1"
DEFAULT_THRESHOLDS = {
    "min_width": 320,
    "min_height": 320,
    "min_entropy": 1.8,
    "min_contrast": 0.04,
    "min_sharpness": 0.003,
    "max_dark_clip": 0.45,
    "max_bright_clip": 0.45,
    "max_brightness_cv": 0.65,
    "min_color_hist_similarity": 0.08,
}


def _media_suffix(path: Path) -> str:
    return path.suffix.lower()


def _find_binary(name: str) -> str:
    override = str(os.getenv("FFMPEG_BINARY" if name == "ffmpeg" else "FFPROBE_BINARY", "") or "").strip()
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    root = Path(__file__).resolve().parents[3]
    candidate = root / "tools" / "ffmpeg" / "bin" / (f"{name}.exe" if os.name == "nt" else name)
    return str(candidate) if candidate.is_file() else ""


def _ppm_tokens(data: bytes, count: int) -> tuple[list[bytes], int]:
    tokens: list[bytes] = []
    index = 0
    size = len(data)
    while len(tokens) < count and index < size:
        while index < size and data[index] in b" \t\r\n":
            index += 1
        if index < size and data[index] == ord("#"):
            while index < size and data[index] not in b"\r\n":
                index += 1
            continue
        start = index
        while index < size and data[index] not in b" \t\r\n":
            index += 1
        if index > start:
            tokens.append(data[start:index])
    return tokens, index


def decode_ppm(raw: bytes) -> dict[str, Any]:
    tokens, offset = _ppm_tokens(raw, 4)
    if len(tokens) != 4 or tokens[0] != b"P6":
        raise ValueError("only binary PPM (P6) is supported")
    width, height, maximum = (int(item) for item in tokens[1:])
    if width <= 0 or height <= 0 or not 0 < maximum <= 255:
        raise ValueError("invalid PPM header")
    # PPM 头部与像素数据之间只有一个分隔符；不能吞掉所有空白字节，否则当
    # 第一个像素恰好是 10/13/32 时会被误删，导致整行颜色错位。
    if offset < len(raw) and raw[offset] == 13 and offset + 1 < len(raw) and raw[offset + 1] == 10:
        offset += 2
    elif offset < len(raw) and raw[offset] in b" \t\r\n":
        offset += 1
    pixels = raw[offset:offset + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("truncated PPM pixel data")
    if maximum != 255:
        pixels = bytes(round(value * 255 / maximum) for value in pixels)
    return {"width": width, "height": height, "rgb": pixels}


def _decode_with_ffmpeg(path: Path, seek_seconds: float = 0.0) -> dict[str, Any]:
    ffmpeg = _find_binary("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg unavailable")
    command = [ffmpeg, "-v", "error"]
    if seek_seconds > 0:
        command.extend(["-ss", f"{seek_seconds:.3f}"])
    command.extend(["-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "ppm", "-"])
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"frame decode failed: {detail}")
    return decode_ppm(result.stdout)


def decode_visual(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        if _media_suffix(path) == ".ppm":
            return decode_ppm(path.read_bytes()), ""
        if _media_suffix(path) in VIDEO_SUFFIXES:
            try:
                return _decode_with_ffmpeg(path, 1.0), ""
            except RuntimeError:
                # 极短视频可能没有 1 秒位置，再退回第一帧重试一次。
                return _decode_with_ffmpeg(path), ""
        return _decode_with_ffmpeg(path), ""
    except Exception as exc:
        if "FFmpeg unavailable" in str(exc):
            return None, "unverified: FFmpeg unavailable"
        return None, f"failed: {exc}"


def _sample(frame: dict[str, Any], maximum: int = 128) -> dict[str, Any]:
    width, height, rgb = frame["width"], frame["height"], frame["rgb"]
    step = max(1, int(math.ceil(max(width, height) / maximum)))
    sampled_width = max(1, math.ceil(width / step))
    sampled_height = max(1, math.ceil(height / step))
    values: list[int] = []
    colors: list[tuple[int, int, int]] = []
    for y in range(0, height, step):
        for x in range(0, width, step):
            index = (y * width + x) * 3
            red, green, blue = rgb[index], rgb[index + 1], rgb[index + 2]
            colors.append((red, green, blue))
            values.append(max(0, min(255, round(0.2126 * red + 0.7152 * green + 0.0722 * blue))))
    return {"width": width, "height": height, "sample_width": sampled_width, "sample_height": sampled_height, "luma": values, "rgb": colors}


def _histogram(values: list[int], bins: int) -> list[float]:
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, max(0, value * bins // 256))] += 1
    total = max(1, len(values))
    return [count / total for count in counts]


def _dhash(luma: list[int], width: int, height: int) -> str:
    rows, columns = 8, 9
    grid: list[float] = []
    for row in range(rows):
        y0, y1 = row * height // rows, max(row * height // rows + 1, (row + 1) * height // rows)
        for column in range(columns):
            x0, x1 = column * width // columns, max(column * width // columns + 1, (column + 1) * width // columns)
            values = [luma[y * width + x] for y in range(y0, y1) for x in range(x0, x1)]
            grid.append(sum(values) / max(1, len(values)))
    bits = []
    for row in range(rows):
        for column in range(columns - 1):
            bits.append("1" if grid[row * columns + column] > grid[row * columns + column + 1] else "0")
    return f"{int(''.join(bits), 2):016x}"


def measure_frame(frame: dict[str, Any], source: str = "") -> dict[str, Any]:
    sampled = _sample(frame)
    luma, colors = sampled["luma"], sampled["rgb"]
    count = max(1, len(luma))
    brightness = sum(luma) / count / 255.0
    variance = sum((value / 255.0 - brightness) ** 2 for value in luma) / count
    contrast = math.sqrt(max(0.0, variance))
    histogram = _histogram(luma, 64)
    entropy = -sum(probability * math.log2(probability) for probability in histogram if probability > 0)
    width, height = sampled["sample_width"], sampled["sample_height"]
    gradient_sum = 0.0
    gradient_count = 0
    for y in range(height):
        for x in range(1, width):
            gradient_sum += abs(luma[y * width + x] - luma[y * width + x - 1])
            gradient_count += 1
        if y:
            for x in range(width):
                gradient_sum += abs(luma[y * width + x] - luma[(y - 1) * width + x])
                gradient_count += 1
    sharpness = gradient_sum / max(1, gradient_count) / 255.0
    dark_clip = sum(1 for value in luma if value <= 8) / count
    bright_clip = sum(1 for value in luma if value >= 247) / count
    rg = [red - green for red, green, _ in colors]
    yb = [(red + green) / 2.0 - blue for red, green, blue in colors]
    rg_mean, yb_mean = sum(rg) / count, sum(yb) / count
    rg_std = math.sqrt(sum((value - rg_mean) ** 2 for value in rg) / count)
    yb_std = math.sqrt(sum((value - yb_mean) ** 2 for value in yb) / count)
    colorfulness = math.sqrt(rg_std ** 2 + yb_std ** 2) / 255.0
    color_hist: list[float] = []
    for red_bin in range(4):
        for green_bin in range(4):
            for blue_bin in range(4):
                color_hist.append(
                    sum(
                        1 for red, green, blue in colors
                        if min(3, red * 4 // 256) == red_bin
                        and min(3, green * 4 // 256) == green_bin
                        and min(3, blue * 4 // 256) == blue_bin
                    ) / count
                )
    return {
        "source": source,
        "width": sampled["width"],
        "height": sampled["height"],
        "brightness": round(brightness, 6),
        "contrast": round(contrast, 6),
        "sharpness": round(sharpness, 6),
        "entropy": round(entropy, 6),
        "dark_clip_ratio": round(dark_clip, 6),
        "bright_clip_ratio": round(bright_clip, 6),
        "colorfulness": round(colorfulness, 6),
        "dhash": _dhash(luma, width, height),
        "color_histogram": [round(value, 6) for value in color_hist],
    }


def quality_verdict(metrics: dict[str, Any], thresholds: dict[str, float] | None = None) -> dict[str, Any]:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[str] = []
    if int(metrics.get("width", 0)) < limits["min_width"] or int(metrics.get("height", 0)) < limits["min_height"]:
        failures.append("分辨率低于验收门槛")
    if float(metrics.get("entropy", 0)) < limits["min_entropy"]:
        failures.append("画面信息量过低")
    if float(metrics.get("contrast", 0)) < limits["min_contrast"]:
        failures.append("画面对比度过低")
    if float(metrics.get("sharpness", 0)) < limits["min_sharpness"]:
        failures.append("画面过软或模糊")
    if float(metrics.get("dark_clip_ratio", 0)) > limits["max_dark_clip"]:
        failures.append("死黑比例过高")
    if float(metrics.get("bright_clip_ratio", 0)) > limits["max_bright_clip"]:
        failures.append("死白比例过高")
    return {"passed": not failures, "failures": failures, "thresholds": limits}


def histogram_similarity(left: list[float], right: list[float]) -> float:
    return round(sum(min(a, b) for a, b in zip(left, right)), 6)


def hamming_hex(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except (TypeError, ValueError):
        return 64


def _asset_info(assets: dict[str, dict[str, Any]], shot: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(shot.get("asset_local_path") or ""))
    media_type = str(shot.get("asset_media_type") or "").strip().lower()
    if not media_type:
        media_type = "video" if _media_suffix(path) in VIDEO_SUFFIXES else "image"
    source = assets.get(str(shot.get("id") or ""), {}).get("source") or ""
    return {
        "shot_id": str(shot.get("id") or ""),
        "order": int(shot.get("order") or 0),
        "path": path,
        "media_type": media_type,
        "source": str(source),
    }


def verify_task_visuals(
    task: dict[str, Any],
    *,
    min_video_shots: int | None = None,
    strict_media: bool = False,
    strict_reference: bool = False,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    shots = [item for item in task.get("shots") or [] if isinstance(item, dict)]
    inputs = task.get("input") if isinstance(task.get("input"), dict) else {}
    knowledge = (task.get("audio") or {}).get("knowledge") if isinstance(task.get("audio"), dict) else {}
    knowledge = knowledge if isinstance(knowledge, dict) else {}
    style_ctx = {
        "city": inputs.get("city", ""),
        "landmark": inputs.get("landmark", ""),
        "theme": inputs.get("theme", ""),
        "style": inputs.get("style", ""),
        "subject_lock": inputs.get("subject_lock", ""),
        "subject_reference_path": inputs.get("subject_reference_path", ""),
        "color": knowledge.get("color", ""),
        "accent": knowledge.get("accent", ""),
        "output_resolution": inputs.get("output_resolution", "720x1280"),
    }
    plan = consistency.plan(str(task.get("id") or ""), shots, style_ctx)
    contract = consistency.validate_plan(plan)
    failures: list[str] = list(contract.get("failures") or [])
    shot_reports: list[dict[str, Any]] = []
    measured: list[dict[str, Any]] = []
    video_count = 0
    assets = {
        str(item.get("shot_id") or ""): item
        for item in task.get("assets") or []
        if isinstance(item, dict)
    }
    for shot in shots:
        asset = _asset_info(assets, shot)
        path = asset["path"]
        if asset["media_type"] == "video":
            video_count += 1
        report: dict[str, Any] = {
            "shot_id": asset["shot_id"],
            "order": asset["order"],
            "media_type": asset["media_type"],
            "path": str(path),
            "source": asset["source"],
        }
        if not path.is_file() or path.stat().st_size <= 0:
            report.update(status="failed", failures=["素材文件不存在或为空"])
            failures.append(f"镜头 {asset['order']} 素材文件不存在或为空")
        else:
            frame, decode_error = decode_visual(path)
            if frame is None:
                status = "unverified" if decode_error.startswith("unverified") else "failed"
                report.update(status=status, failures=[decode_error])
                # 未检查也不能算通过。strict_media 只决定是否立刻阻断编排；
                # 独立验收始终把“无法解码”视为未通过。
                failures.append(f"镜头 {asset['order']} {decode_error}")
            else:
                metrics = measure_frame(frame, source=str(path))
                verdict = quality_verdict(metrics, thresholds)
                report.update(status="passed" if verdict["passed"] else "failed", metrics=metrics, failures=verdict["failures"])
                measured.append({**metrics, "shot_id": asset["shot_id"], "order": asset["order"]})
                if not verdict["passed"]:
                    failures.append(f"镜头 {asset['order']} 画面质量未通过")
        shot_reports.append(report)
    required_videos = int(min_video_shots or 0)
    if video_count < required_videos:
        failures.append(f"视频镜头 {video_count} 个，低于要求 {required_videos} 个")
    cross_shot: dict[str, Any] = {"measured_shots": len(measured)}
    if len(measured) >= 2:
        brightness = [float(item["brightness"]) for item in measured]
        mean = sum(brightness) / len(brightness)
        cv = math.sqrt(sum((value - mean) ** 2 for value in brightness) / len(brightness)) / max(mean, 0.01)
        similarities = [
            histogram_similarity(left["color_histogram"], right["color_histogram"])
            for index, left in enumerate(measured)
            for right in measured[index + 1:]
        ]
        min_similarity = min(similarities) if similarities else 1.0
        distances = [
            hamming_hex(left["dhash"], right["dhash"])
            for index, left in enumerate(measured)
            for right in measured[index + 1:]
        ]
        cross_shot.update(
            brightness_cv=round(cv, 6),
            min_color_hist_similarity=round(min_similarity, 6),
            max_perceptual_hash_distance=max(distances) if distances else 0,
        )
        if cv > DEFAULT_THRESHOLDS["max_brightness_cv"]:
            failures.append("跨镜亮度波动过大，疑似曝光/风格漂移")
        if min_similarity < DEFAULT_THRESHOLDS["min_color_hist_similarity"]:
            failures.append("跨镜色彩分布差异过大，疑似跑片")
    reference_path = Path(str(inputs.get("subject_reference_path") or ""))
    reference: dict[str, Any] = {"path": str(reference_path) if str(reference_path) != "." else ""}
    if str(reference_path) != "." and reference_path.is_file() and measured:
        frame, decode_error = decode_visual(reference_path)
        if frame is not None:
            ref_metrics = measure_frame(frame, source=str(reference_path))
            distances = [hamming_hex(ref_metrics["dhash"], item["dhash"]) for item in measured]
            similarities = [histogram_similarity(ref_metrics["color_histogram"], item["color_histogram"]) for item in measured]
            reference.update(
                sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                mean_hash_distance=round(sum(distances) / len(distances), 3),
                mean_color_hist_similarity=round(sum(similarities) / len(similarities), 6),
                enforced=bool(strict_reference),
            )
            if strict_reference and reference["mean_color_hist_similarity"] < DEFAULT_THRESHOLDS["min_color_hist_similarity"]:
                failures.append("镜头与主体参考图的色彩签名差异过大")
        else:
            reference.update(error=decode_error, enforced=bool(strict_reference))
            if strict_reference:
                failures.append("主体参考图无法解码验收")
    return {
        "version": QA_VERSION,
        "task_id": str(task.get("id") or ""),
        "passed": not failures,
        "requirements": {
            "shot_count": len(shots),
            "min_video_shots": required_videos,
            "video_shot_count": video_count,
            "strict_media": bool(strict_media),
            "strict_reference": bool(strict_reference),
        },
        "generation_contract": contract,
        "shots": shot_reports,
        "cross_shot": cross_shot,
        "subject_reference": reference,
        "failures": failures,
        "limitations": [
            "视频画质以约第 1 秒的代表帧测量，用于筛查黑场、空帧、过软和曝光异常，不等同于逐帧质量检测。",
            "跨镜指标是亮度、色彩直方图和感知哈希的漂移筛查，不等同于语义人物识别。",
            "需要语义级身份核验时，应接入主体分割、人脸识别或视觉嵌入模型，并把相似度阈值写入验收。",
        ],
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def verify_media_file(path: Path) -> dict[str, Any]:
    """单文件辅助函数，便于媒体 provider 在下载后立即做客观检查。"""
    if not path.is_file() or path.stat().st_size <= 0:
        return {"passed": False, "status": "failed", "failures": ["素材文件不存在或为空"]}
    frame, error = decode_visual(path)
    if frame is None:
        return {"passed": False, "status": "unverified" if error.startswith("unverified") else "failed", "failures": [error]}
    metrics = measure_frame(frame, source=str(path))
    verdict = quality_verdict(metrics)
    return {"passed": verdict["passed"], "status": "passed" if verdict["passed"] else "failed", "metrics": metrics, "failures": verdict["failures"]}
