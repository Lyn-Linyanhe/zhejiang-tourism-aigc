"""Pure validation helpers for the audio upload API."""

from __future__ import annotations

from typing import Any
from ..models import ALLOWED_MUSIC_MOODS
from ..providers.music import authorized


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

    mood = str(data.get("mood") or task.get("input", {}).get("music_mood") or "").strip()
    if mood not in ALLOWED_MUSIC_MOODS[:-1]:
        raise ValueError("请选择有效的配乐情绪")
    return {
        **upload_music_rights(data), "kind": kind, "moods": [mood],
        "styles": [str(task.get("input", {}).get("style", "")).strip()],
    }


def upload_music_rights(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("approved", True) is not True:
        raise ValueError("上传音乐需要具备使用授权")
    fields = {key: str(data.get(key) or "").strip() for key in ("license", "author", "usage_scope", "authorization_ref")}
    if any(len(value) > 200 for value in fields.values()):
        raise ValueError("授权信息每项最多 200 字")
    record = {
        "author": fields["author"] or "上传者提供曲目（未填作者）",
        "license": fields["license"] or "上传曲目完整授权",
        "usage_scope": fields["usage_scope"] or "文旅短视频制作、竞赛展示与公开发布",
        "approved": True,
        "authorization_basis": "uploader_assertion",
        "authorization_ref": fields["authorization_ref"],
    }
    if not authorized(record):
        raise ValueError("授权说明需与已获授权的状态一致")
    return record
