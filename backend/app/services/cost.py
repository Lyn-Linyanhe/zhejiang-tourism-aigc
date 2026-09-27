"""成本台账。

数据源只有 AssetResult.cost（契约已定义）。最贵的调用是画面生成，
所以把每次调用的 cost 加总，不在这里另算单价。
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
