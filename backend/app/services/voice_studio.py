"""Shared narration planning for the studio, previews and final rendering.

These are phrase-level controls, not measured F0 curves or singing notes.
Keep Mandarin lexical tones and intra-sentence timing with the neural engine.
"""
from __future__ import annotations

import math
import re
from typing import Any

STYLES = {
    "natural": {"label": "自然讲述", "rate": 1.0, "pitch": 0},
    "warm": {"label": "温暖舒缓", "rate": 0.96, "pitch": -2},
    "story": {"label": "人文叙事", "rate": 0.94, "pitch": -3},
    "bright": {"label": "轻快邀约", "rate": 1.04, "pitch": 3},
    "neutral": {"label": "原声直读", "rate": 1.0, "pitch": 0},
}
VOICE_LIBRARY = [
    {"voice": "晓晓·温暖女声", "style": "warm", "description": "温柔导览 · 湖山风景"},
    {"voice": "云希·清朗男声", "style": "natural", "description": "亲切讲述 · 城市漫游"},
    {"voice": "云健·沉稳男声", "style": "story", "description": "沉稳叙事 · 历史人文"},
    {"voice": "晓伊·活泼女声", "style": "bright", "description": "轻快明亮 · 青春出游"},
]


def bounded_number(value: Any, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字") from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f"{name} 必须在 {low} 到 {high} 之间")
    return number


def validate_clip(raw: dict[str, Any]) -> dict[str, Any]:
    style = str(raw.get("voice_style", "inherit"))
    if style not in {"inherit", *STYLES}:
        raise ValueError("未知的片段语气")
    pause = raw.get("voice_pause_after")
    return {
        "voice_style": style,
        "voice_rate_scale": bounded_number(raw.get("voice_rate_scale", 1), 0.75, 1.25, "片段语速"),
        "voice_pitch_offset": bounded_number(raw.get("voice_pitch_offset", 0), -20, 20, "片段音高"),
        "voice_pause_after": None if pause is None or pause == "" else bounded_number(pause, 0, 1.5, "片段停顿"),
    }


def split_sentences(text: str) -> list[str]:
    # Commas/list separators remain inside the phrase. Decimal numbers and
    # English abbreviations are not split on '.'; repeated punctuation and
    # closing quotes stay with the preceding sentence.
    return [part.strip() for part in re.findall(r'[^。！？!?\n]+[。！？!?]*[”’」』\"]*|[。！？!?]+', str(text))
            if re.search(r"[\w\u4e00-\u9fff]", part)]


def plan_segments(text: str, subtitle: str) -> list[tuple[str, str]]:
    narration, captions = split_sentences(text), split_sentences(subtitle)
    if len(narration) > 1 and len(narration) == len(captions):
        return list(zip(narration, captions))
    return [(text, subtitle)]


def phrase_parameters(text: str, *, style: str, strength: float, rate: float,
                      pitch: float, clip: Any) -> dict[str, Any]:
    options = validate_clip(vars(clip))
    selected = options["voice_style"] if options["voice_style"] != "inherit" else style
    profile = STYLES[selected]
    amount = 0.0 if selected == "neutral" else strength
    pitch_delta = float(profile["pitch"])
    speed_delta = float(profile["rate"]) - 1
    reason = profile["label"]
    if re.search(r"[？?][”’」』\"]*$", text):
        pitch_delta += 4
        speed_delta -= 0.02
        reason += " · 疑问"
    elif re.search(r"[！!][”’」』\"]*$", text):
        pitch_delta += 3
        speed_delta += 0.02
        reason += " · 感叹"
    elif re.search(r"[。][”’」』\"]*$", text):
        pitch_delta -= 2
        reason += " · 陈述"
    return {
        "style": selected, "reason": reason,
        "rate": round(max(0.5, min(2.0, rate * options["voice_rate_scale"] * (1 + speed_delta * amount))), 3),
        "pitch_hz": round(max(-50, min(50, pitch + options["voice_pitch_offset"] + pitch_delta * amount))),
    }


def plan_track(shots: list[Any], task_input: Any) -> dict[str, Any]:
    clips = []
    elapsed = 0.0
    for index, shot in enumerate(shots):
        uploaded = bool(shot.audio_local_path)
        segments = [(shot.narration, shot.subtitle)] if uploaded else plan_segments(shot.narration, shot.subtitle or shot.narration)
        phrases = []
        for text, caption in segments:
            params = phrase_parameters(text, style=task_input.voice_style,
                strength=task_input.voice_expressiveness, rate=task_input.voice_rate,
                pitch=task_input.voice_pitch, clip=shot)
            phrases.append({"text": text, "subtitle": caption, **params})
        duration = max(0.1, float(shot.duration_sec))
        pause = shot.voice_pause_after if shot.voice_pause_after is not None else 0.15
        clips.append({"shot_id": shot.id, "order": shot.order, "start": round(elapsed, 3),
                      "end": round(elapsed + duration, 3), "phrases": phrases,
                      "uploaded": uploaded, "pause_after": pause if index < len(shots)-1 else 0})
        elapsed += duration
    return {"clips": clips, "duration": round(elapsed, 3), "timing": "nominal", "parameter_scope": "phrase"}
