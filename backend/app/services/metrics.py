"""Validation for recorded billing documents and platform-reported outcomes."""

from __future__ import annotations

import math
from datetime import date
from urllib.parse import urlsplit
from typing import Any


ALLOWED_METRICS = {"views", "reach", "impressions", "likes", "comments", "shares", "saves", "clicks", "watch_time_seconds", "followers_gained"}
MAX_OBSERVATIONS_PER_TASK = 500
MAX_BILLING_RECORDS_PER_TASK = 200


def _date(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date")
    return value


def _http_url(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an HTTP(S) URL")
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be an HTTP(S) URL")
    return value.strip()


def validate_billing_record(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Billing record must be an object")
    try:
        amount = float(raw.get("amount"))
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be a non-negative finite number") from exc
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("amount must be a non-negative finite number")
    currency = str(raw.get("currency") or "").strip()
    if len(currency) != 3 or not currency.isalpha() or not currency.isascii() or currency != currency.upper():
        raise ValueError("currency must be an uppercase 3-letter ISO code")
    source = str(raw.get("source") or "").strip()
    if not source or len(source) > 500:
        raise ValueError("source is required and must be at most 500 characters")
    note = str(raw.get("note") or "").strip()
    if len(note) > 1000:
        raise ValueError("note must be at most 1000 characters")
    return {
        "amount": round(amount, 6),
        "currency": currency,
        "source": source,
        "captured_at": _date(raw.get("captured_at"), "captured_at"),
        "note": note,
    }


def validate_publication_observation(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Publication observation must be an object")
    platform = str(raw.get("platform") or "").strip().lower()
    if platform not in {"douyin", "xiaohongshu", "bilibili", "youtube", "weibo", "wechat-channels", "other"}:
        raise ValueError("platform is unsupported")
    post_url = _http_url(raw.get("post_url"), "post_url")
    published_at = _date(raw.get("published_at"), "published_at")
    observed_at = _date(raw.get("observed_at"), "observed_at")
    if observed_at < published_at:
        raise ValueError("observed_at must not precede published_at")
    try:
        window_days = int(raw.get("window_days"))
    except (TypeError, ValueError) as exc:
        raise ValueError("window_days must be between 1 and 365") from exc
    if isinstance(raw.get("window_days"), bool) or not 1 <= window_days <= 365:
        raise ValueError("window_days must be between 1 and 365")
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError("metrics must include at least one reported metric")
    normalized_metrics: dict[str, float | int] = {}
    for name, raw_value in metrics.items():
        if name not in ALLOWED_METRICS:
            raise ValueError(f"unsupported metric: {name}")
        if isinstance(raw_value, bool):
            raise ValueError(f"{name} must be a non-negative number")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a non-negative number") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a non-negative number")
        normalized_metrics[name] = int(value) if value.is_integer() else round(value, 3)
    source_note = str(raw.get("source_note") or "").strip()
    if len(source_note) > 1000:
        raise ValueError("source_note must be at most 1000 characters")
    return {
        "platform": platform,
        "post_url": post_url,
        "published_at": published_at,
        "observed_at": observed_at,
        "window_days": window_days,
        "metrics": normalized_metrics,
        "source_note": source_note,
    }


def observation_key(record: dict[str, Any]) -> tuple[str, str, int]:
    return str(record["platform"]), str(record["post_url"]).rstrip("/"), int(record["window_days"])


def summarize_task_metrics(task: dict[str, Any]) -> dict[str, Any]:
    from .cost import summarize_billing, summarize_cost, summarize_execution

    return {
        "cost": summarize_cost(task),
        "billing": summarize_billing(task.get("billing_records")),
        "execution": summarize_execution(task),
        "publication_observations": list(task.get("publication_observations") or []),
    }
