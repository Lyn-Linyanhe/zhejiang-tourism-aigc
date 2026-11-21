"""Build the task's auditable production and billing record from saved evidence."""

from __future__ import annotations

import json
import re
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..models import now_iso
from . import knowledge
from .visual_review import read_review, write_json

_lock = threading.RLock()
_SETTLEMENTS = "cost-settlements.json"
_LOCAL = {"offline-zhejiang-editor", "offline-scene-generator", "offline", "local-upload", "local-library", "local-catalog",
          "task-upload", "ffmpeg", "windows-system-speech", "edge-tts", "pexels", "pixabay", "stock", "none"}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cost_ledger(task: dict, directory: Path) -> dict:
    trace = (task.get("trace") or {}).get("events") or []
    receipts = _read_json(directory / _SETTLEMENTS)
    rows: dict[str, dict] = {}

    def add(key: str, event: dict, slot: str, *, known_local: bool = False) -> None:
        provider = str(event.get("provider") or event.get("source") or "")
        model = str(event.get("model") or "")
        remote_id = event.get("remote_task_id")
        if remote_id:
            key = f"remote:{provider}:{remote_id}"
        entry = rows.setdefault(key, {"id": key, "slot": slot, "provider": provider, "model": model,
                                      "billing_status": "no_external_charge" if known_local else "pending"})
        for field in ("shot_id", "purpose", "request_id", "remote_task_id", "usage", "ok", "at", "cache_hit", "receipt_status", "http_status"):
            if field in event and event[field] is not None:
                entry[field] = event[field]
        if model:
            entry["model"] = model
        if event.get("cost"):
            entry["provider_reported_cost"] = event["cost"]

    for index, event in enumerate(trace, 1):
        if event.get("type") == "billing_call":
            add(f"event:{index}", event, event["slot"])
        elif event.get("type") == "llm":
            if event.get("provider") in _LOCAL or not event.get("billing_instrumented"):
                add(f"event:{index}", event, "llm", known_local=event.get("provider") in _LOCAL)
        elif event.get("type") in {"asset", "video", "image", "tts", "music", "render"}:
            provider = event.get("provider")
            if event.get("remote_task_id") or not event.get("billing_instrumented"):
                slots = {"tts": "audio", "music": "audio", "render": "render"}
                add(f"event:{index}", event, slots.get(event["type"], "asset"), known_local=provider in _LOCAL)

    for preview in sorted(directory.glob("voice-previews/*/billing.json")):
        for index, event in enumerate(_read_json(preview).get("events", []), 1):
            add(f"preview:{preview.parent.name}:{index}", event, "audio")

    # Remote job receipts survive processing failures and record calls from older runs.
    for receipt in sorted(directory.glob("assets/**/*.remote-task.json")):
        saved = _read_json(receipt)
        if saved.get("remote_task_id"):
            shot_asset = next((asset for asset in task.get("assets", []) if asset.get("shot_id") == saved.get("shot_id")), {})
            add("", {"model": shot_asset.get("model", ""), **saved,
                     "receipt_status": saved.get("status", "")}, "asset")

    assets = task.get("assets") or []
    for asset in assets:
        if not any(row.get("shot_id") == asset.get("shot_id") and row["slot"] == "asset"
                   and row["provider"] == asset.get("source") and row.get("purpose") != "first_frame"
                   for row in rows.values()):
            add(f"asset:{asset['shot_id']}", asset, "asset", known_local=asset.get("source") in _LOCAL)
        if asset.get("source") == "cloud-i2v" and asset.get("first_frame_path") and not any(
            row.get("shot_id") == asset.get("shot_id") and row.get("purpose") == "first_frame"
            for row in rows.values()
        ):
            add(f"legacy-first-frame:{asset['shot_id']}", {"shot_id": asset["shot_id"], "purpose": "first_frame"},
                "asset")

    audio = task.get("audio") or {}
    for index, item in enumerate(audio.get("provider_trace") or [], 1):
        if item.get("provider") in _LOCAL or item.get("cache_hit"):
            add(f"audio:{index}", item, "audio", known_local=True)
        elif not item.get("billing_instrumented"):
            add(f"legacy-audio:{index}", item, "audio")
    music = audio.get("music_match") or {}
    if music.get("id"):
        add("music", music, "audio", known_local=music.get("provider") in _LOCAL)
    if (render := task.get("render") or {}).get("video_url"):
        add("render", {"provider": render.get("render_provider") or "ffmpeg"}, "render", known_local=True)

    totals: dict[str, Decimal] = {}
    for key, row in rows.items():
        if key in receipts:
            row["settlement"] = receipts[key]
            row["billing_status"] = "reconciled"
            unit = receipts[key]["currency"]
            totals[unit] = totals.get(unit, Decimal(0)) + Decimal(receipts[key]["amount"])
    pending = sum(row["billing_status"] == "pending" for row in rows.values())
    return {"entries": list(rows.values()), "pending_count": pending,
            "actual_by_currency": {k: str(v) for k, v in sorted(totals.items())},
            "complete": bool(rows) and pending == 0,
            "cost_scope": "external_provider_charges"}


def reconcile(task: dict, directory: Path, data: dict) -> dict:
    key = data.get("entry_id")
    currency = str(data.get("currency") or "").strip().upper()
    ref = str(data.get("reference") or "").strip()
    raw = str(data.get("amount", "")).strip()
    if not re.fullmatch(r"[A-Z]{3}", currency) or not ref or len(ref) > 500:
        raise ValueError("请输入三位币种代码与账单编号或凭证链接")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,8})?", raw):
        raise ValueError("请输入账单中的非负金额，最多八位小数")
    with _lock:
        entry = next((row for row in cost_ledger(task, directory)["entries"] if row["id"] == key), None)
        if not entry or entry["billing_status"] == "no_external_charge":
            raise ValueError("该条目没有待对账的外部服务费用")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _SETTLEMENTS
        saved = _read_json(path)
        old = saved.get(key)
        history = old.get("history", []) + [{k: v for k, v in old.items() if k != "history"}] if old else []
        saved[key] = {"amount": str(Decimal(raw)), "currency": currency, "reference": ref,
                      "recorded_at": now_iso(), "history": history}
        write_json(path, saved)
    return cost_ledger(task, directory)


def task_ledger(task: dict[str, Any], directory: Path, music_catalog: Path | None = None) -> dict:
    shots = task.get("shots") or []
    audio = task.get("audio") or {}
    task_input = task.get("input") or {}
    music = audio.get("music_match") or {}
    catalog = _read_json(music_catalog) if music_catalog else {}
    authorization = next((row for row in catalog.get("tracks", [])
                          if music.get("id") and row.get("id") == music["id"]
                          and row.get("authorization_basis") == "uploader_assertion"), None)
    if authorization:
        authorization = {key: authorization.get(key) for key in ("id", "file", "license", "author", "usage_scope",
                                                                 "approved", "authorization_basis", "authorization_ref")}
    visual_assets = []
    for asset in task.get("assets") or []:
        row = dict(asset)
        source = asset.get("source")
        if source in {"pexels", "pixabay"}:
            row["authorization_basis"] = "platform_license"
            row["license_terms_url"] = {"pexels": "https://www.pexels.com/license/",
                                        "pixabay": "https://pixabay.com/service/license-summary/"}[source]
            if source == "pexels" and not row.get("page_url"):
                match = re.fullmatch(r"Pexels (photo|video) (\d+)", str(row.get("name") or ""))
                if match:
                    kind, identifier = match.groups()
                    row["page_url"] = f"https://www.pexels.com/{kind}/{identifier}/"
                    row["page_url_basis"] = "derived_from_legacy_platform_id"
        elif asset.get("authorization_ref"):
            row["authorization_basis"] = "recorded_permission_reference"
        elif asset.get("is_ai_generated") and source not in {"offline", "offline-scene-generator"}:
            row["authorization_basis"] = "provider_terms_pending"
        else:
            row["authorization_basis"] = "recorded_asset_metadata"
        visual_assets.append(row)
    return {
        "schema_version": 1,
        "task_id": task["id"], "status": task.get("status"), "stage": task.get("stage"),
        "created_at": task.get("created_at"), "updated_at": task.get("updated_at"),
        "input": task_input, "initial_input": task.get("initial_input"),
        "script": {key: task.get(key) for key in ("title", "summary", "narration")},
        "knowledge_references": audio.get("knowledge_references", []),
        "shots": [{"id": shot["id"], "title": shot.get("title"), "visual_prompt": shot.get("visual_prompt"),
                   "narration": shot.get("narration"), "subtitle": shot.get("subtitle"),
                   "knowledge_matches": knowledge.shot_references(task_input.get("city", ""), shot)}
                  for shot in shots],
        "visual_assets": visual_assets,
        "audio": {key: audio.get(key) for key in ("voice_provider", "voice_model", "provider_trace",
                                                  "music_provider", "music_match", "music_upload", "cues", "subtitle_alignment")},
        "music_authorization": authorization,
        "render": task.get("render"),
        "visual_review": read_review(directory) if task.get("status") == "completed" else None,
        "billing": cost_ledger(task, directory),
        "trace": task.get("trace"), "error": task.get("error", ""),
    }
