"""为报告生成配图：数据合同关系、任务状态机、知识注入 A/B 实测、Provider 降级链。

输出到 runtime/report-assets/。字体用 SimHei，中文字形按 Windows 系统字体渲染。
"""

from __future__ import annotations

import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.sans-serif"] = ["SimHei"]
plt.rcParams["axes.unicode_minus"] = False

OUT = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "report-assets"
OUT.mkdir(parents=True, exist_ok=True)

INK = "#1F3B63"
ACCENT = "#2E6DB4"
FILL = "#E8F0F8"
FILL2 = "#D3E2F2"
GOLD = "#C8A45C"
GOLD_FILL = "#FAF3E4"
GREY = "#8A94A6"
GREY_FILL = "#F2F4F7"
TEAL = "#2D6C63"
TEAL_FILL = "#E4F0EE"


def _canvas(w: float, h: float):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def _box(ax, x, y, w, h, text, *, fill=FILL, edge=ACCENT, size=10.5, weight="normal",
         color=INK, radius=1.6, lw=1.3):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2,
    ))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=size, color=color, weight=weight, zorder=3, linespacing=1.5)


def _arrow(ax, p1, p2, *, color=ACCENT, style="-|>", lw=1.3, dashed=False, rad=0.0):
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle=style, mutation_scale=13, linewidth=lw,
        color=color, zorder=1, connectionstyle=f"arc3,rad={rad}",
        linestyle="--" if dashed else "-",
        shrinkA=1, shrinkB=1,
    ))


def _label(ax, x, y, text, *, size=9, color=GREY, ha="center", weight="normal"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=color, weight=weight)


# ---------------------------------------------------------------- 图 3-3 数据合同
def fig_contract(path: pathlib.Path) -> None:
    fig, ax = _canvas(9.4, 5.6)

    _box(ax, 7, 76, 38, 16, "创作输入\n城市 / 主题 / 时长 / 规格",
         fill=GOLD_FILL, edge=GOLD, size=9.5)
    _box(ax, 7, 46, 38, 16, "任务\n状态 · 阶段 · 进度 · 文案 · 成片结果",
         fill=FILL2, edge=ACCENT, size=9.5, weight="bold")
    _box(ax, 7, 14, 38, 16, "镜头\n顺序 · 时长 · 画面 · 旁白 · 字幕 · 转场",
         fill=FILL, edge=ACCENT, size=9.5)
    _box(ax, 56, 46, 38, 16, "任务追溯记录\n能力方 · 模型 · 降级 · 阶段事件",
         fill=TEAL_FILL, edge=TEAL, size=9.5)
    _box(ax, 56, 14, 38, 16, "素材档案\n来源 · 许可 · 作者 · 使用范围 · 路径",
         fill=TEAL_FILL, edge=TEAL, size=9.5)

    _arrow(ax, (26, 76), (26, 62.6))
    _label(ax, 28.4, 69.4, "1:1", ha="left")

    _arrow(ax, (26, 46), (26, 30.6))
    _label(ax, 28.4, 38.4, "1:N 分镜", ha="left")

    _arrow(ax, (45.4, 54), (55.6, 54))
    _label(ax, 50.5, 58.2, "1:1 追溯", size=8.6)

    _arrow(ax, (45.4, 22), (55.6, 22))
    _label(ax, 50.5, 26.2, "按镜头关联", size=8.6)

    _label(ax, 50, 4.4, "四个稳定数据对象构成整条生产链的合同：上游只写这些字段，下游只读这些字段",
           size=9, color=GREY)

    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 图 3-4 状态机
def fig_state(path: pathlib.Path) -> None:
    fig, ax = _canvas(11.4, 5.6)

    stages = [
        ("scripting", "① 脚本"),
        ("storyboard", "② 分镜"),
        ("audio", "③ 配音"),
        ("assets", "④ 画面"),
        ("rendering", "⑤ 渲染"),
        ("review", "⑥ 审核"),
    ]
    x0, w, gap, y, h = 6.0, 12.4, 2.5, 66, 13
    xs = []
    for index, (_key, title) in enumerate(stages):
        x = x0 + index * (w + gap)
        xs.append(x)
        last = index == len(stages) - 1
        _box(ax, x, y, w, h, title, size=11,
             fill=TEAL_FILL if last else FILL,
             edge=TEAL if last else ACCENT,
             weight="bold")

    for index in range(len(stages) - 1):
        _arrow(ax, (xs[index] + w, y + h / 2), (xs[index + 1], y + h / 2))

    # 人工闸门：分镜与配音之间
    gate_x = xs[2] - gap / 2
    ax.plot([gate_x, gate_x], [y, y + h + 2.5], color=GOLD, lw=1.5, linestyle=":", zorder=1)
    _box(ax, gate_x - 8.0, y + h + 3.6, 16.0, 8.0, "用户确认闸门",
         fill=GOLD_FILL, edge=GOLD, size=9.5, weight="bold", color="#8A6A22")

    # 失败总线：①②③④⑤ 都可落到 failed
    bus_y = 50
    first_cx = xs[0] + w / 2
    last_cx = xs[4] + w / 2
    ax.plot([first_cx, last_cx], [bus_y, bus_y], color="#C0563F", lw=1.1,
            linestyle="--", zorder=1)
    for index in range(5):
        _arrow(ax, (xs[index] + w / 2, y), (xs[index] + w / 2, bus_y),
               color="#C0563F", dashed=True, lw=1.1)

    _box(ax, 31, 17, 38, 12, "失败\n记录错误信息与阶段事件",
         fill="#FBE9E7", edge="#C0563F", size=9.5, color="#8C3A25")

    mid = (first_cx + last_cx) / 2
    _arrow(ax, (mid - 4, bus_y), (mid - 4, 29.6), color="#C0563F", dashed=True, lw=1.1)
    _arrow(ax, (mid + 4, 29.6), (mid + 4, bus_y), color="#C0563F", dashed=True, lw=1.1)
    _label(ax, mid + 6.5, 39.5, "从指定阶段重试", size=8.8, color="#8C3A25", ha="left")

    _label(ax, 50, 8.0,
           "预览阶段只执行 ①②，用户确认后才触发 ③④⑤⑥；重试时已确认的脚本、分镜与已落盘素材继续复用",
           size=9, color=GREY)
    _label(ax, 50, 93, "任务状态机与阶段重试", size=12, weight="bold", color=INK)

    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 图 4-1 知识注入 A/B
def fig_ab(path: pathlib.Path) -> None:
    fig, ax = plt.subplots(figsize=(7.6, 4.3))

    metrics = ["知识实体命中", "具体名词使用", "事实性表述"]
    without = [4.75, 2.5, 2.25]
    with_injection = [6.25, 17.5, 13.5]

    positions = range(len(metrics))
    width = 0.34

    bars_a = ax.bar([p - width / 2 for p in positions], without, width,
                    label="A｜无知识注入", color="#B9C4D4", edgecolor="white", linewidth=1.2, zorder=3)
    bars_b = ax.bar([p + width / 2 for p in positions], with_injection, width,
                    label="B｜注入知识参考块", color=ACCENT, edgecolor="white", linewidth=1.2, zorder=3)

    for bars in (bars_a, bars_b):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.45,
                    f"{bar.get_height():g}", ha="center", va="bottom",
                    fontsize=10, color=INK, weight="bold")

    ax.set_xticks(list(positions))
    ax.set_xticklabels(metrics, fontsize=10.5, color=INK)
    ax.set_ylabel("每篇脚本平均出现次数", fontsize=10, color=INK)
    ax.set_ylim(0, 20.5)
    ax.legend(frameon=False, fontsize=10, loc="upper left")
    ax.grid(axis="y", color="#E3E7EE", linewidth=0.9, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#C7CEDB")
    ax.tick_params(axis="both", length=0, colors=INK)

    ax.set_title("知识注入前后脚本质量对比（同一模型、同一组参数，各采样 4 次）",
                 fontsize=11.5, color=INK, pad=12, weight="bold")

    fig.tight_layout()
    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 图 4-2 降级链
def fig_chain(path: pathlib.Path) -> None:
    fig, ax = _canvas(11.6, 6.8)

    rows = [
        ("文本", ["商用大模型", "离线编排器"], ""),
        ("画面 · 图生视频", ["图生视频", "AI 静帧", "本地素材库", "离线占位"], ""),
        ("画面 · 文生视频", ["文生视频"], "严格模式：仅此一档，失败即任务失败"),
        ("画面 · 在线素材", ["在线素材库", "离线占位"], ""),
        ("配音", ["在线语音合成", "系统语音", "合成测试音"], ""),
        ("音乐", ["本地授权曲库"], ""),
        ("渲染", ["本地视频合成"], ""),
    ]

    y = 88.5
    row_h = 11.8
    box_w, gap = 14.8, 3.4
    for title, chain, note in rows:
        _label(ax, 25.0, y + 4.7, title, size=10, color=INK, ha="right", weight="bold")
        for index, name in enumerate(chain):
            x = 28.0 + index * (box_w + gap)
            first = index == 0
            longest = max(len(line) for line in name.split("\n"))
            _box(ax, x, y, box_w, 9.4, name, size=8.8 if longest <= 16 else 7.5,
                 fill=FILL2 if first else GREY_FILL,
                 edge=ACCENT if first else GREY,
                 color=INK if first else "#5C6675",
                 weight="bold" if first else "normal")
            if index:
                _arrow(ax, (x - gap, y + 4.7), (x, y + 4.7), color=GREY, lw=1.1)
        if note:
            _label(ax, 28.0 + box_w + gap + 1.5, y + 4.7, note,
                   size=8.6, color="#8C3A25", ha="left")
        y -= row_h

    _label(ax, 50, 4.0,
           "深色为默认首选，灰底为降级档；素材链最终回落到离线占位，AI 画面失败则如实报错",
           size=9, color=GREY)

    fig.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    fig_contract(OUT / "fig-contract.png")
    fig_state(OUT / "fig-state-machine.png")
    fig_ab(OUT / "fig-kb-ab.png")
    fig_chain(OUT / "fig-provider-chain.png")
    for name in ("fig-contract", "fig-state-machine", "fig-kb-ab", "fig-provider-chain"):
        print(name, (OUT / f"{name}.png").stat().st_size)


if __name__ == "__main__":
    main()
