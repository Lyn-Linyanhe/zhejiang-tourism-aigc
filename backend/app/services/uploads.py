"""Pure validation helpers for the audio upload API."""

from __future__ import annotations

from typing import Any


def validate_audio_upload(data: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    kind = str(data.get("kind", "")).strip().lower()
    if kind not in {"voice", "music"}:
        raise ValueError("kind must be voice or music")
    if kind == "voice":
        shot_id = str(data.get("shot_id", "")).strip()
        if not shot_id:
            raise ValueError("shot_id is required for every voice upload")
        if not any(str(shot.get("id")) == shot_id for shot in task.get("shots", [])):
            raise ValueError("shot_id does not belong to this task")
        return {"kind": kind, "shot_id": shot_id}

    required = {
        key: str(data.get(key, "")).strip()
        for key in ("mood", "license", "author", "usage_scope")
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError("Music upload requires " + ", ".join(missing))
    if data.get("approved") is not True:
        raise ValueError("Music upload requires approved=true after license confirmation")
    required.update(
        {
            "kind": kind,
            "approved": True,
            "styles": [str(task.get("input", {}).get("style", "")).strip()],
            "moods": [required.pop("mood")],
        }
    )
    return required
