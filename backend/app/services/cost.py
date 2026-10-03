from __future__ import annotations

from typing import Any


def summarize_cost(task: dict[str, Any]) -> dict[str, Any]:
    """Summarize provider-reported costs without assuming a billing currency."""
    by_slot = {"llm": 0.0, "asset": 0.0, "audio": 0.0, "render": 0.0}
    type_to_slot = {
        "llm": "llm", "asset": "asset", "image": "asset", "video": "asset",
        "tts": "audio", "music": "audio", "render": "render",
    }
    events = (task.get("trace") or {}).get("events") or []
    for event in events:
        if not isinstance(event, dict):
            continue
        slot = str(event.get("slot") or type_to_slot.get(str(event.get("type", "")), ""))
        if slot not in by_slot:
            continue
        try:
            by_slot[slot] += max(0.0, float(event.get("cost", 0) or 0))
        except (TypeError, ValueError):
            continue
    rounded = {key: round(value, 6) for key, value in by_slot.items()}
    return {
        "total": round(sum(rounded.values()), 6),
        "by_slot": rounded,
        "unit": "provider-reported",
        "complete": all("cost" in event for event in events if isinstance(event, dict) and event.get("type") in type_to_slot),
    }
