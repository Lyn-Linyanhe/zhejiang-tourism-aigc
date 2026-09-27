"""六个镜头共用的种子、风格前缀、负向词和调色。

同一任务的画面必须像一部片子，而不是六张互不相关的图。策略固定为：

- 种子：任务基点 + 镜头序号（从 1 起）。基点相同，镜头之间只差序号，
  既能复现，又不会六张图完全一样。
- 风格：同一段前缀、同一段负向词。颜色和气质优先取 style_ctx
  （知识库 color / accent），缺省用宋韵水墨竖屏前缀。
- 调色：一份 grade 字典，交给 P1 写入 RenderRequest.grade，由 P4 做 FFmpeg eq。
"""

from __future__ import annotations

import hashlib
from typing import Any


SHOT_COUNT = 6

# 2**31-1。文生图 seed 和方舟视频 seed 都落在这个范围内。
SEED_MODULUS = 2_147_483_647

# 宋韵水墨是本项目缺省气质。style_ctx 里有更具体的风格时会拼在前面。
DEFAULT_STYLE_PREFIX = (
    "宋韵水墨，青瓷与淡金，江南烟雨，竖屏短片同一套光色，"
    "电影感，写实纹理，镜头稳定"
)

DEFAULT_NEGATIVE_PROMPT = (
    "文字，水印，logo，边框，畸形手指，多余肢体，"
    "现代广告牌，低清晰度，过饱和，卡通，风格跳变"
)

# 对应 P4 的 eq=saturation:contrast:brightness。数值克制，避免六镜之间色调跳。
DEFAULT_GRADE = {
    "saturation": 1.06,
    "contrast": 1.04,
    "brightness": 0.01,
}


def task_seed_base(task_id: str, style_ctx: dict[str, Any] | None = None) -> int:
    """同一 task_id 永远得到同一个基点。显式 base_seed 优先。"""
    style_ctx = style_ctx or {}
    explicit = style_ctx.get("base_seed", style_ctx.get("seed_base"))
    if explicit is not None and str(explicit).strip() != "":
        return int(explicit) % SEED_MODULUS
    digest = hashlib.sha256(str(task_id or "task").encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % SEED_MODULUS


def shot_order(shot: dict[str, Any], index: int = 1) -> int:
    raw = shot.get("order", index)
    try:
        order = int(raw)
    except (TypeError, ValueError):
        order = index
    return order if order >= 1 else 1


def seed_for_shot(
    task_id: str,
    shot: dict[str, Any],
    style_ctx: dict[str, Any] | None = None,
    index: int = 1,
) -> int:
    """基点 + 镜头序号。镜头 1 用基点本身，之后每镜加一。"""
    base = task_seed_base(task_id, style_ctx)
    return (base + shot_order(shot, index) - 1) % SEED_MODULUS


def seeds_for_task(
    task_id: str,
    shot_count: int = SHOT_COUNT,
    style_ctx: dict[str, Any] | None = None,
) -> list[int]:
    """按镜头 1..N 展开种子。缺省六个镜头。"""
    count = shot_count if shot_count >= 1 else SHOT_COUNT
    return [
        seed_for_shot(task_id, {"order": order}, style_ctx, order)
        for order in range(1, count + 1)
    ]


def style_prefix(style_ctx: dict[str, Any] | None = None) -> str:
    """六个镜头共用的风格前缀。调用方传入的 prefix 原样使用。"""
    style_ctx = style_ctx or {}
    explicit = str(style_ctx.get("style_prefix") or style_ctx.get("prefix") or "").strip()
    if explicit:
        return explicit
    parts: list[str] = []
    style = str(style_ctx.get("style") or "").strip()
    if style:
        parts.append(style)
    color = str(style_ctx.get("color") or "").strip()
    accent = str(style_ctx.get("accent") or "").strip()
    if color or accent:
        parts.append(f"主色{color or '未指定'}，点缀{accent or '未指定'}")
    parts.append(DEFAULT_STYLE_PREFIX)
    return "，".join(parts)


def negative_prompt(style_ctx: dict[str, Any] | None = None) -> str:
    """六个镜头共用的负向词。显式 negative 优先，否则用缺省。"""
    style_ctx = style_ctx or {}
    explicit = str(
        style_ctx.get("negative_prompt") or style_ctx.get("negative") or ""
    ).strip()
    return explicit or DEFAULT_NEGATIVE_PROMPT


def compose_prompt(visual_prompt: str, style_ctx: dict[str, Any] | None = None) -> str:
    """风格前缀在前，镜头画面在后。前缀为空时不额外加分隔符。"""
    body = str(visual_prompt or "").strip()
    prefix = style_prefix(style_ctx)
    if prefix and body:
        return f"{prefix}。{body}"
    return prefix or body


def grade(style_ctx: dict[str, Any] | None = None) -> dict[str, float]:
    """给 RenderRequest.grade 的调色参数。缺字段时回退缺省值。"""
    style_ctx = style_ctx or {}
    raw = style_ctx.get("grade") if isinstance(style_ctx.get("grade"), dict) else {}
    result: dict[str, float] = {}
    for key, default in DEFAULT_GRADE.items():
        value = raw.get(key, style_ctx.get(key, default))
        try:
            result[key] = float(value)
        except (TypeError, ValueError):
            result[key] = float(default)
    return result


def plan(
    task_id: str,
    shots: list[dict[str, Any]] | None = None,
    style_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一次任务的完整一致性方案：六个种子、同一前缀、同一负向词、一份 grade。"""
    style_ctx = style_ctx or {}
    items = list(shots) if shots else [{"order": order} for order in range(1, SHOT_COUNT + 1)]
    prefix = style_prefix(style_ctx)
    negative = negative_prompt(style_ctx)
    planned = []
    for index, shot in enumerate(items, start=1):
        visual = str(shot.get("visual_prompt") or "")
        planned.append(
            {
                "order": shot_order(shot, index),
                "seed": seed_for_shot(task_id, shot, style_ctx, index),
                "prompt": compose_prompt(visual, style_ctx),
                "negative_prompt": negative,
                "style_prefix": prefix,
            }
        )
    return {
        "task_id": str(task_id or ""),
        "seed_base": task_seed_base(task_id, style_ctx),
        "style_prefix": prefix,
        "negative_prompt": negative,
        "grade": grade(style_ctx),
        "shots": planned,
    }
