"""成本台账。

数据源只有各 provider 上报的 cost（契约已定义）。最贵的调用是画面生成，
所以把每次调用的 cost 加总，不在这里另算单价。

本文件是合并产物：P2 的按次台账（CostLedger / sum_costs）与 P1 的按 slot
汇总（summarize_cost）各服务一端，二者都保留。
"""

from __future__ import annotations

from typing import Any, Iterable


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
