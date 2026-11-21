"""Attach measured word boundaries to the approved subtitle text."""

from __future__ import annotations

import math
from pathlib import Path


def letters(text):
    return "".join(ch.casefold() for ch in text if ch.isalnum())


def phrase_cues(cue, words):
    """Only accept timestamped words that account for the displayed text."""
    text = cue["text"]
    indices = [i for i, ch in enumerate(text) if ch.isalnum()]
    if not indices or not words:
        return []
    pos = 0
    pieces = []
    last_end = cue["start"]
    for word in words:
        token = letters(word["text"])
        if not token:
            continue
        start, end = (
            max(float(word["start"]), cue["start"]),
            min(float(word["end"]), cue["end"]),
        )
        if (
            not math.isfinite(start + end)
            or start < last_end - 0.04
            or end <= max(last_end, start)
        ):
            return []
        start = max(start, last_end)
        if pos + len(token) > len(indices):
            return []
        left = indices[pos] if pos else 0
        pos += len(token)
        right = indices[pos] if pos < len(indices) else len(text)
        part = text[left:right]
        if letters(part) != token:
            return []
        pieces.append({"start": start, "end": end, "text": part})
        last_end = end
    if pos != len(indices):
        return []
    groups = []
    for part in pieces:
        if (
            groups
            and len(groups[-1]["text"] + part["text"]) <= 14
            and part["start"] - groups[-1]["end"] < 0.35
            and groups[-1]["text"][-1] not in "，。！？；、："
        ):
            groups[-1].update(text=groups[-1]["text"] + part["text"], end=part["end"])
        else:
            groups.append(dict(part, shot_id=cue["shot_id"]))
    return groups


def align_subtitles(audio, runtime, *, sentence_only=False):
    cues = audio.get("cues", [])
    if not audio.get("voice_path") or not cues:
        return
    tracks = {
        i: {"source": "sentence", "reason": "缺少可用词时间戳", "words": []}
        for i in range(len(cues))
    }
    result = {i: [cue] for i, cue in enumerate(cues)}
    traces = audio.get("provider_trace", [])
    expected = [
        traces[i].get("text", cue["text"]) if i < len(traces) else cue["text"]
        for i, cue in enumerate(cues)
    ]
    pending = {
        i for i, c in enumerate(cues) if letters(c["text"]) == letters(expected[i])
    }
    for i in tracks.keys() - pending:
        tracks[i]["reason"] = "字幕与旁白措辞不同，保留编辑文本和句级边界"
    warnings = []
    path = Path(audio["voice_path"])
    segments = [
        dict(start=c["start"], end=c["end"], text=s) for c, s in zip(cues, expected)
    ]
    if sentence_only:
        for i in pending:
            tracks[i]["reason"] = "已选择离线句级字幕"
        pending.clear()
    for source in ("whisperx", "whisper"):
        if not pending:
            break
        try:
            payload = (
                runtime.align(path, segments=segments)
                if source == "whisperx"
                else runtime.transcribe(path)
            )
            words = [
                w
                for seg in payload.get("segments", [])
                for w in seg.get("words", [])
                if w.get("text")
            ]
            for i in sorted(pending):
                cue = cues[i]
                selected = [
                    w
                    for w in words
                    if cue["start"] <= (w["start"] + w["end"]) / 2 < cue["end"]
                ]
                phrases = phrase_cues(cue, selected)
                if phrases:
                    result[i] = phrases
                    tracks[i] = {"source": source, "words": selected}
                    pending.remove(i)
                else:
                    tracks[i]["reason"] = "识别文本或时间边界与已确认字幕不一致"
        except Exception as exc:
            warnings.append(
                f"{source}: {(str(exc).splitlines() or [type(exc).__name__])[-1][:200]}"
            )
    for i, track in tracks.items():
        track.update(
            shot_id=cues[i]["shot_id"], start=cues[i]["start"], end=cues[i]["end"]
        )
    audio["subtitle_cues"] = [c for phrases in result.values() for c in phrases]
    audio["subtitle_alignment"] = {
        "segments": list(tracks.values()),
        "warnings": warnings,
    }
