"""Export a task's script and storyboard without calling any provider."""

from __future__ import annotations

import json
import re
from typing import Any


_FORMATS = {"md": "md", "markdown": "md", "txt": "txt", "text": "txt", "json": "json"}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _cell(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")


def _metadata(task: dict[str, Any]) -> dict[str, Any]:
    data = task.get("input") or {}
    trace = task.get("trace") or {}
    return {
        "city": _text(data.get("city")),
        "landmark": _text(data.get("landmark")),
        "theme": _text(data.get("theme")),
        "style": _text(data.get("style")),
        "duration": data.get("duration"),
        "output_resolution": _text(data.get("output_resolution")),
        "fps": data.get("fps"),
        "voice": _text(data.get("voice")),
        "music_mood": _text(data.get("music_mood")),
        "visual_usage": _text(data.get("visual_usage")),
        "provider": _text(trace.get("provider")),
        "model": _text(trace.get("model")),
        "fallback_used": bool(trace.get("fallback_used")),
        "created_at": _text(task.get("created_at")),
        "updated_at": _text(task.get("updated_at")),
    }


def _shots(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [shot for shot in (task.get("shots") or []) if isinstance(shot, dict)]


def _filename(task: dict[str, Any], suffix: str) -> str:
    task_id = re.sub(r"[^A-Za-z0-9_-]+", "_", _text(task.get("id")) or "script")[:60]
    return f"script_{task_id}.{suffix}"


def _markdown(task: dict[str, Any]) -> str:
    meta = _metadata(task)
    lines = [
        f"# {_text(task.get('title')) or '未命名脚本'}",
        "",
        f"> {_text(task.get('summary'))}",
        "",
        f"- 城市 / 地标：{meta['city']} / {meta['landmark'] or '未指定'}",
        f"- 主题 / 风格：{meta['theme']} / {meta['style']}",
        f"- 时长 / 画幅 / 帧率：{meta['duration']} 秒 / {meta['output_resolution']} / {meta['fps']} fps",
        f"- 配音 / 音乐：{meta['voice']} / {meta['music_mood']}",
        f"- 文本 provider：{meta['provider'] or 'pending'} / {meta['model'] or 'pending'} / fallback={meta['fallback_used']}",
        f"- 更新时间：{meta['updated_at'] or meta['created_at'] or '未知'}",
        "",
        "## 全片旁白",
        "",
        _text(task.get("narration")) or "（空）",
        "",
        "## 分镜",
        "",
        "| 镜号 | 时长 | 画面 | 旁白 | 字幕 | 转场 |",
        "|---:|---:|---|---|---|---|",
    ]
    shots = _shots(task)
    if not shots:
        lines.append("| - | - | （无分镜） | - | - | - |")
    for index, shot in enumerate(shots, start=1):
        lines.append(
            "| {index} | {duration} | {visual} | {narration} | {subtitle} | {transition} |".format(
                index=shot.get("order") or index,
                duration=shot.get("duration_sec", ""),
                visual=_cell(shot.get("visual_prompt")),
                narration=_cell(shot.get("narration")),
                subtitle=_cell(shot.get("subtitle")),
                transition=_cell(shot.get("transition")),
            )
        )
    lines.extend(["", "## 行动号召", "", _text(task.get("cta")) or "（空）", ""])
    return "\n".join(lines)


def _plain(task: dict[str, Any]) -> str:
    meta = _metadata(task)
    lines = [
        f"标题：{_text(task.get('title'))}",
        f"摘要：{_text(task.get('summary'))}",
        f"城市 / 地标：{meta['city']} / {meta['landmark'] or '未指定'}",
        f"主题 / 风格：{meta['theme']} / {meta['style']}",
        f"时长 / 画幅 / 帧率：{meta['duration']} 秒 / {meta['output_resolution']} / {meta['fps']} fps",
        f"文本 provider：{meta['provider'] or 'pending'} / {meta['model'] or 'pending'} / fallback={meta['fallback_used']}",
        "",
        "全片旁白：",
        _text(task.get("narration")) or "（空）",
        "",
        "分镜：",
    ]
    for index, shot in enumerate(_shots(task), start=1):
        lines.extend(
            [
                f"[镜头 {shot.get('order') or index}] {shot.get('duration_sec', '')} 秒 / 转场 {shot.get('transition') or '-'}",
                f"画面：{_text(shot.get('visual_prompt'))}",
                f"旁白：{_text(shot.get('narration'))}",
                f"字幕：{_text(shot.get('subtitle'))}",
                "",
            ]
        )
    lines.extend(["行动号召：", _text(task.get("cta")) or "（空）", ""])
    return "\n".join(lines)


def _json_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": task.get("id"),
        "title": _text(task.get("title")),
        "summary": _text(task.get("summary")),
        "cta": _text(task.get("cta")),
        "narration": _text(task.get("narration")),
        "tags": list(task.get("tags") or []),
        "metadata": _metadata(task),
        "shots": [
            {
                "id": shot.get("id"),
                "order": shot.get("order"),
                "duration_sec": shot.get("duration_sec"),
                "title": _text(shot.get("title")),
                "visual_prompt": _text(shot.get("visual_prompt")),
                "narration": _text(shot.get("narration")),
                "subtitle": _text(shot.get("subtitle")),
                "transition": _text(shot.get("transition")),
                "entity_id": _text(shot.get("entity_id")),
                "shot_tag": _text(shot.get("shot_tag")),
            }
            for shot in _shots(task)
        ],
    }


def export_script(task: dict[str, Any], fmt: str = "md") -> tuple[bytes, str, str]:
    """Return (body, content_type, filename) for a task script export."""
    normalized = _FORMATS.get(str(fmt or "md").strip().lower())
    if not normalized:
        raise ValueError("format must be md, txt, or json")
    if normalized == "md":
        return _markdown(task).encode("utf-8"), "text/markdown; charset=utf-8", _filename(task, "md")
    if normalized == "txt":
        return _plain(task).encode("utf-8"), "text/plain; charset=utf-8", _filename(task, "txt")
    payload = json.dumps(_json_payload(task), ensure_ascii=False, indent=2)
    return payload.encode("utf-8"), "application/json; charset=utf-8", _filename(task, "json")
