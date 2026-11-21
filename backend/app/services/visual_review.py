"""Collect evidence from the delivered video and retain per-shot editorial decisions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from ..models import now_iso
from . import knowledge

_lock = threading.RLock()
_FILENAME = "visual-review.json"
_DECISIONS = {"match", "partial", "mismatch", "unverifiable"}


def write_json(path: Path, data: dict[str, Any]) -> None:
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as file:
        temporary = Path(file.name)
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _video_digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _file_stamp(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def read_review(directory: Path) -> dict[str, Any] | None:
    try:
        report = json.loads((directory / _FILENAME).read_text(encoding="utf-8"))
        if report["file_stamp"] == _file_stamp(directory / "final.mp4"):
            return _with_summary(report)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _with_summary(report: dict) -> dict:
    rows = report["shots"]
    counts = dict.fromkeys(["pending", "match", "partial", "mismatch", "unverifiable"], 0)
    for row in rows:
        counts[row["decision"]["status"]] += 1
    technical_flags = sum(row["technical"] != "complete" for row in rows)
    if technical_flags or counts["mismatch"]:
        status = "needs_revision"
    elif counts["pending"] or not rows:
        status = "pending"
    elif counts["partial"] or counts["unverifiable"]:
        status = "needs_verification"
    else:
        status = "passed"
    return {**report, "summary": {"status": status, "decisions": counts, "technical_flags": technical_flags}}


def probe_delivery(video: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,codec_name,r_frame_rate:format=duration",
         "-of", "json", str(video)],
        check=True, capture_output=True, timeout=20,
    )
    data = json.loads(result.stdout)
    if not data.get("streams") or not data.get("format", {}).get("duration"):
        raise ValueError("成片中没有视频流")
    stream = data["streams"][0]
    return {
        **stream,
        "duration_sec": float(data["format"]["duration"]),
        "vertical": stream["height"] > stream["width"],
    }


def _sample(video: Path, at: float, target: Path) -> dict:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "info", "-y", "-ss", str(at), "-i", str(video),
         "-frames:v", "1", "-vf", "scale=270:480:force_original_aspect_ratio=decrease,blackframe=97:18",
         "-q:v", "3", "-update", "1", str(target)],
        check=True, capture_output=True, timeout=20,
    )
    if not target.is_file() or not target.stat().st_size:
        raise ValueError("成片取样未返回画面")
    return {"second": at, "near_black": b"pblack:" in result.stderr}


def build_review(task: dict[str, Any], directory: Path) -> dict:
    video = directory / "final.mp4"
    stamp = _file_stamp(video)
    digest = _video_digest(video)
    render = task.get("render") or {}
    shots = task.get("shots") or []
    assets = {a["shot_id"]: a for a in task.get("assets") or []}
    timeline = {r["shot_id"]: r for r in render.get("shot_timeline") or []}
    transitions = render.get("transitions") or []
    delivery = probe_delivery(video)
    maximum = delivery["duration_sec"]
    frame = 1 / float((task.get("input") or {}).get("fps") or 25)
    version = directory / "visual-review" / digest[:16]
    version.mkdir(parents=True, exist_ok=True)
    rows = []
    elapsed = 0.0
    for index, shot in enumerate(shots):
        key = shot["id"]
        asset = assets.get(key, {})
        interval = timeline.get(key, {})
        start = float(interval.get("start", elapsed))
        elapsed += float(shot["duration_sec"])
        end = min(maximum, float(interval.get("end", maximum if index == len(shots)-1 else elapsed)))
        prior = transitions[index-1] if 0 < index <= len(transitions) else {}
        following = transitions[index] if index < len(transitions) else {}
        left = min(end, start + float(prior.get("duration_sec", 0)) / 2)
        right = max(left, end - float(following.get("duration_sec", 0)) / 2)
        row = {
            "shot_id": key, "order": index + 1, "title": shot.get("title", ""),
            "start": round(start, 3), "end": round(end, 3),
            "visual_prompt": shot.get("visual_prompt", ""), "narration": shot.get("narration", ""),
            "targets": knowledge.shot_references(task.get("input", {}).get("city", ""), shot),
            "asset": {k: asset.get(k) for k in ("id", "source", "page_url", "location_evidence", "is_ai_generated")},
            "samples": [], "technical": "complete", "decision": {"status": "pending"},
        }
        if start >= end:
            row["technical"] = "outside_final"
        else:
            for slot, fraction in enumerate((.15, .5, .85), 1):
                point = left + (right-left) * fraction if right > left else (start + end) / 2
                at = round(max(start, min(point, end - frame)), 4)
                target = version / f"{index+1:02d}-{slot}.jpg"
                try:
                    evidence = _sample(video, at, target)
                    evidence["url"] = f"/media/{task['id']}/{target.relative_to(directory).as_posix()}"
                    row["samples"].append(evidence)
                except (OSError, ValueError, subprocess.SubprocessError):
                    row["technical"] = "sample_error"
            if row["technical"] == "complete" and any(sample["near_black"] for sample in row["samples"]):
                row["technical"] = "near_black"
        rows.append(row)
    report = {"version": 1, "task_id": task["id"], "video_sha256": digest,
              "file_stamp": stamp, "generated_at": now_iso(), "shots": rows, "delivery": delivery,
              "timeline_source": "renderer" if timeline else "saved_shot_durations"}
    with _lock:
        if stamp != _file_stamp(video):
            raise ValueError("成片已变化，请重新抽帧")
        previous = read_review(directory) or {}
        if previous.get("video_sha256") == digest:
            existing = {r["shot_id"]: r for r in previous.get("shots", [])}
            fields = ("title", "start", "end", "narration", "visual_prompt", "asset")
            for row in rows:
                old = existing.get(row["shot_id"])
                if old and all(old.get(key) == row[key] for key in fields):
                    row["decision"] = old["decision"]
                    if old.get("decision_history"):
                        row["decision_history"] = old["decision_history"]
        write_json(directory / _FILENAME, _with_summary(report))
    return _with_summary(report)


def record_review(directory: Path, data: dict[str, Any]) -> dict:
    status = data.get("status")
    note = str(data.get("note") or "").strip()
    reviewer = str(data.get("reviewer") or "").strip()
    if not isinstance(status, str) or status not in _DECISIONS or not note or len(note) > 1000 or len(reviewer) > 80:
        raise ValueError("请选择核验结论并填写具体观察，观察最多 1000 字")
    with _lock:
        report = read_review(directory)
        if (not report or report["video_sha256"] != data.get("video_sha256")
                or _video_digest(directory / "final.mp4") != report["video_sha256"]):
            raise ValueError("成片证据已更新，请重新打开核验")
        row = next((item for item in report["shots"] if item["shot_id"] == data.get("shot_id")), None)
        if row is None:
            raise KeyError("镜头不存在")
        if row["decision"]["status"] != "pending":
            row.setdefault("decision_history", []).append(row["decision"])
        row["decision"] = {"status": status, "note": note, "reviewer": reviewer, "reviewed_at": now_iso()}
        write_json(directory / _FILENAME, _with_summary(report))
    return _with_summary(report)
