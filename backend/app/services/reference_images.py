"""为每一镜挑参考图，供图生图使用。

参考图池来自 `docs/research/xhs-zhejiang-image-index.csv`（小红书采集的 322 张，
38 个点位）。这些图是 `reference-only`：**只作为图生图的输入**，决定构图、机位和
质感；生成结果是一张新图，原图不会被输出、不会进素材库、也不会进成片。
因此用它当参考不构成对原作者的再分发。

匹配策略按可靠度从高到低：

1. 城市 + 点位（spot）都对上，且 shot_tag 与图片所在笔记的角度一致；
2. 城市 + 点位对上即可（tag 只是偏好，不强制）；
3. 都不对上 → 返回空串，调用方退回纯文生图。

不做跨城市兜底：给杭州的镜头配一张绍兴的参考图，构图会把人带偏，比没有参考更糟。
"""

from __future__ import annotations

import csv
import pathlib
import random
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[3]
INDEX = ROOT / "docs/research/xhs-zhejiang-image-index.csv"


def _load() -> list[dict[str, str]]:
    if not INDEX.exists():
        return []
    with INDEX.open(encoding="utf-8-sig") as handle:
        rows = [row for row in csv.DictReader(handle) if row.get("file")]
    return [row for row in rows if (ROOT / row["file"]).is_file()]


_CACHE: list[dict[str, str]] | None = None


def _rows() -> list[dict[str, str]]:
    global _CACHE
    if _CACHE is None:
        _CACHE = _load()
    return _CACHE


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def _spot_candidates(city: str, spot: str) -> list[dict[str, str]]:
    rows = _rows()
    city = (city or "").strip()
    spot = (spot or "").strip()
    exact = [r for r in rows if r["city"] == city and r["spot"] == spot]
    if exact:
        return exact
    if not spot:
        return [r for r in rows if r["city"] == city]
    return []


def find_reference(
    *,
    city: str = "",
    spot: str = "",
    shot_tag: str = "",
    seed: str = "",
) -> str:
    """返回一张参考图的本地路径；没有合适参考时返回空串。

    `seed` 用镜头 id：同一镜重跑会挑到同一张参考，保证可复现；
    不同镜头挑到不同图，避免整条片子都用同一张参考导致画面雷同。
    """
    candidates = _spot_candidates(city, spot)
    if not candidates:
        return ""
    preferred = [r for r in candidates if (r.get("shot_tag") or "") == shot_tag] if shot_tag else []
    pool = preferred or candidates
    # 按文件名排序后取模，保证同 seed 稳定、不同 seed 分散
    ordered = sorted(pool, key=lambda r: (r["note_id"], r["image_index"]))
    index = (hash(seed) % len(ordered)) if seed else 0
    return str(ROOT / ordered[index]["file"])


def stats() -> dict[str, Any]:
    rows = _rows()
    spots = {(r["city"], r["spot"]) for r in rows}
    return {
        "images": len(rows),
        "spots": len(spots),
        "cities": len({r["city"] for r in rows}),
    }
