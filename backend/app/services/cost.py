"""Provider-reported cost summaries, kept separate from billing records."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable
from urllib.error import HTTPError

from ..models import now_iso


_billing_sink = ContextVar("billing_sink", default=None)


@contextmanager
def billing_scope(sink):
    token = _billing_sink.set(sink)
    try:
        yield
    finally:
        _billing_sink.reset(token)


@contextmanager
def provider_call(slot: str, provider: str, model: str):
    event = {"type": "billing_call", "slot": slot, "provider": provider, "model": model, "at": now_iso(), "ok": False}
    try:
        yield event
        event["ok"] = True
    except HTTPError as exc:
        event["http_status"] = exc.code
        record_response(event, exc)
        raise
    finally:
        sink = _billing_sink.get()
        if sink:
            sink(event)


def record_response(event: dict, response, payload: dict | None = None) -> None:
    headers = getattr(response, "headers", None) or {}
    data = payload or {}
    request_id = headers.get("x-request-id") or headers.get("request-id") or data.get("request_id") or data.get("id")
    if isinstance(request_id, str):
        event["request_id"] = request_id
    usage = data.get("usage")
    if isinstance(usage, dict):
        event["usage"] = usage



def _cost_of(item: Any) -> float:
    if isinstance(item, dict):
        raw = item.get("cost", 0.0)
    else:
        raw = getattr(item, "cost", 0.0)
    try:
        value = float(raw or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"AssetResult.cost 不是数字：{raw!r}") from exc
    if value < 0:
        raise ValueError("AssetResult.cost 不能为负")
    return value


def sum_costs(results: Iterable[Any]) -> float:
    """把每次 AssetResult.cost 加总。空列表是 0。"""
    total = 0.0
    for item in results:
        total += _cost_of(item)
    return total


class CostLedger:
    """按次记下 cost，再给出合计。不改 AssetResult 本身。"""

    def __init__(self) -> None:
        self._entries: list[dict[str, Any]] = []

    def record(self, result: Any, *, label: str = "") -> float:
        cost = _cost_of(result)
        self._entries.append(
            {
                "label": label,
                "source": str(getattr(result, "source", "") or (result.get("source") if isinstance(result, dict) else "")),
                "model": str(getattr(result, "model", "") or (result.get("model") if isinstance(result, dict) else "")),
                "cost": cost,
            }
        )
        return cost

    def total(self) -> float:
        return sum_costs(self._entries)

    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)


def summarize_cost(task: dict[str, Any]) -> dict[str, Any]:
    """Summarize only amounts explicitly reported by providers.

    A missing amount is never interpreted as a free call. Provider totals have
    no implied currency and are distinct from manually entered billing records.
    """
    by_slot = {"llm": 0.0, "asset": 0.0, "audio": 0.0, "render": 0.0}
    type_to_slot = {
        "llm": "llm", "asset": "asset", "image": "asset", "video": "asset",
        "tts": "audio", "music": "audio", "audio": "audio", "render": "render",
    }
    events = (task.get("trace") or {}).get("events") or []
    billable_events: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        slot = str(event.get("slot") or type_to_slot.get(str(event.get("type", "")), ""))
        if slot not in by_slot:
            continue
        # Stage and fallback events are operational metadata, not billable calls.
        if str(event.get("type", "")) not in type_to_slot and not event.get("billable"):
            continue
        billable_events.append(event)

    reported_total = 0.0
    missing_count = 0
    reported_count = 0
    for event in billable_events:
        if "cost" not in event or event.get("cost_reported") is not True:
            missing_count += 1
            continue
        try:
            amount = float(event["cost"])
        except (TypeError, ValueError):
            missing_count += 1
            continue
        if amount < 0:
            missing_count += 1
            continue
        slot = str(event.get("slot") or type_to_slot.get(str(event.get("type", "")), ""))
        by_slot[slot] += amount
        reported_total += amount
        reported_count += 1

    if not billable_events or reported_count == 0:
        amount_status = "not_reported"
    elif missing_count:
        amount_status = "incomplete"
    else:
        amount_status = "reported"
    rounded_slots = {key: round(value, 6) for key, value in by_slot.items()}
    return {
        "amount_status": amount_status,
        "total": round(reported_total, 6) if amount_status == "reported" else None,
        "reported_total": round(reported_total, 6),
        "by_slot": rounded_slots,
        "currency": None,
        "unit": "provider-reported",
        "complete": amount_status == "reported",
        "reported_event_count": reported_count,
        "missing_event_count": missing_count,
    }


def summarize_billing(records: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Group manually documented actual charges by currency; never mix currencies."""
    totals: dict[str, float] = {}
    for record in records or []:
        currency = str(record.get("currency") or "")
        if not currency:
            continue
        totals[currency] = totals.get(currency, 0.0) + float(record.get("amount", 0) or 0)
    return {
        "records": list(records or []),
        "by_currency": {currency: round(amount, 6) for currency, amount in sorted(totals.items())},
        "source": "manually-documented-billing",
    }


def summarize_execution(task: dict[str, Any]) -> dict[str, Any]:
    runs = [dict(run) for run in task.get("execution_runs") or [] if isinstance(run, dict)]
    total = sum(max(0.0, float(run.get("duration_seconds", 0) or 0)) for run in runs)
    return {
        "runs": runs,
        "completed_run_count": sum(1 for run in runs if run.get("status") == "completed"),
        "failed_run_count": sum(1 for run in runs if run.get("status") == "failed"),
        "total_seconds": round(total, 3) if runs else None,
        "last_seconds": round(float(runs[-1]["duration_seconds"]), 3) if runs and "duration_seconds" in runs[-1] else None,
    }


def summarize_task_metrics(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "cost": summarize_cost(task),
        "billing": summarize_billing(task.get("billing_records")),
        "execution": summarize_execution(task),
        "publication_observations": list(task.get("publication_observations") or []),
    }
