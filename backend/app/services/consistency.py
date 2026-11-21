"""同一任务的视觉生成契约：主体锁定、提示词、种子、负向词和调色。

同一任务的画面必须像一部片子，而不是六张互不相关的图。策略固定为：

- 种子：任务基点 + 镜头序号（从 1 起）。基点相同，镜头之间只差序号，
  既能复现，又不会六张图完全一样。
- 主体：所有镜头携带同一段主体描述；如果提供参考图，则所有镜头记录同一个
  参考图 SHA-256。主体描述和参考图是生成条件，风格词本身不能替代它们。
- 风格：同一段前缀、同一段负向词。颜色和气质优先取 style_ctx
  （知识库 color / accent），缺省使用中性的浙江文旅纪实前缀。
- 调色：一份 grade 字典，交给 P1 写入 RenderRequest.grade，由 P4 做 FFmpeg eq。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


SHOT_COUNT = 6

# 2**31-1。文生图 seed 和方舟视频 seed 都落在这个范围内。
SEED_MODULUS = 2_147_483_647

# 缺省前缀只描述可复用的摄影基线，不强行把每一种选题都压成水墨风。
DEFAULT_STYLE_PREFIX = (
    "浙江地域文旅纪实短片，统一的自然光与环境色，真实材质与比例，"
    "克制电影级调色，竖屏构图，单镜画面稳定"
)

DEFAULT_NEGATIVE_PROMPT = (
    "文字，水印，logo，边框，畸形手指，多余肢体，"
    "现代广告牌，低清晰度，过饱和，卡通，风格跳变，"
    "主体替换，换脸，服装突变，标志物消失，建筑或山体结构变形，"
    "多主体混入，镜头内剪辑，闪烁，第一帧与后续帧不一致"
)

SUBJECT_LOCK_VERSION = "subject-lock-v1"
PROMPT_VERSION = "zj-tourism-prompt-v2"

# 对应 P4 的 eq=saturation:contrast:brightness。数值克制，避免六镜之间色调跳。
DEFAULT_GRADE = {
    "saturation": 1.06,
    "contrast": 1.04,
    "brightness": 0.01,
}


# 输出规格 → 画面 provider 各自认的参数口径。
#
# 关键事实：图生视频继承首帧的宽高比（实测 1440x3040 的首帧出 704x1472），
# 所以成片画幅是由文生图的 size 决定的，只给视频端 ratio 没有用。
# size 用 2 的倍数且贴近 9:16 / 16:9 / 1:1，避免渲染时再裁掉大量画面。
#
# 这里刻意不映射视频分辨率：AI 视频分辨率是按账号/模型计价的，同一网关对
# 720P 返回"当前模型未配置该分辨率的价格"、对 768P 才放行。推断不出来，
# 所以交给 VIDEO_RESOLUTION 由运维按账号配置，成片尺寸仍由渲染器按用户选择输出。
_OUTPUT_SPECS: dict[str, dict[str, str]] = {
    "720x1280": {"ratio": "9:16", "size": "1440x2560"},
    "1080x1920": {"ratio": "9:16", "size": "1440x2560"},
    "1920x1080": {"ratio": "16:9", "size": "2560x1440"},
    "1080x1080": {"ratio": "1:1", "size": "2048x2048"},
}


def output_spec(resolution: str, fallback_size: str = "") -> dict[str, str]:
    """把用户选的成片规格翻成画面 provider 的参数。认不出就只带 size 兜底。"""
    spec = _OUTPUT_SPECS.get(str(resolution or "").strip().lower())
    if spec:
        return dict(spec)
    return {"size": fallback_size} if fallback_size else {}


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


def _subject_reference_path(style_ctx: dict[str, Any]) -> str:
    raw = style_ctx.get("subject_reference_path") or style_ctx.get("reference_image_path")
    return str(raw or "").strip()


def _sha256_file(path: str) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    digest = hashlib.sha256()
    with candidate.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def subject_lock(style_ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回本片唯一的身份/形制约束，并给其稳定 ID。

    这不是把风格词换个名字：subject_id 和 descriptor 会在每个镜头的 prompt 中
    原样复用，参考图路径也会生成 SHA-256 供验收核对。显式 subject_lock 缺失时
    以城市与核心地标建立地理主体锚点，避免每镜各自重新设计地标。
    """
    style_ctx = style_ctx or {}
    explicit = style_ctx.get("subject_lock")
    metadata: dict[str, Any] = dict(explicit) if isinstance(explicit, dict) else {}
    descriptor = str(metadata.get("descriptor") or metadata.get("description") or "").strip()
    if not descriptor:
        descriptor = str(explicit or "").strip()
    city = str(style_ctx.get("city") or "").strip()
    landmark = str(style_ctx.get("landmark") or "").strip()
    theme = str(style_ctx.get("theme") or "").strip()
    if not descriptor:
        place = f"{city}{landmark}" if city and landmark else (landmark or city or "本片核心景点")
        descriptor = f"{place}的代表性主体及其可辨识轮廓、材质和标志性细节"
    traits = str(metadata.get("traits") or "轮廓、结构、材质、主色与标志性细节").strip()
    reference_path = str(
        metadata.get("reference_path")
        or style_ctx.get("subject_reference_path")
        or style_ctx.get("reference_image_path")
        or ""
    ).strip()
    identity_seed = "|".join((descriptor, city, landmark, theme))
    subject_id = "subject_" + hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:12]
    return {
        "version": SUBJECT_LOCK_VERSION,
        "id": subject_id,
        "descriptor": descriptor,
        "traits": traits,
        "city": city,
        "landmark": landmark,
        "reference_path": reference_path,
        "reference_sha256": _sha256_file(reference_path),
        "required": bool(style_ctx.get("subject_lock_required", False)),
    }


def subject_prompt(style_ctx: dict[str, Any] | None = None) -> str:
    lock = subject_lock(style_ctx)
    ref = "；参考图作为身份/形制基准" if lock["reference_sha256"] else ""
    return (
        f"全片主体锁定[{lock['id']}]：{lock['descriptor']}{ref}。"
        f"每个镜头保持相同{lock['traits']}，只改变机位、景别、动作与光线，"
        "不得重新设计、替换或让主体在镜头内变形"
    )


def shot_constraints(shot: dict[str, Any] | None = None) -> str:
    shot = shot or {}
    motion = str(shot.get("motion") or "auto").strip().lower()
    motion_text = {
        "push": "缓慢推近，运动连续",
        "pull": "缓慢拉远，运动连续",
        "gentle": "轻微稳定运动，不晃镜",
    }.get(motion, "机位运动克制且连续")
    return (
        f"生成约束：单镜连续画面，{motion_text}；主体比例、朝向和标志性细节跨帧稳定；"
        "不切镜、不叠化、不新增无依据的主要人物或地标；不做风格突变"
    )


def compose_prompt(
    visual_prompt: str,
    style_ctx: dict[str, Any] | None = None,
    shot: dict[str, Any] | None = None,
    index: int = 1,
) -> str:
    """组装可验收的分层 prompt：风格 → 主体锁定 → 本镜内容 → 生成约束。"""
    body = str(visual_prompt or "").strip()
    style_ctx = style_ctx or {}
    prefix = style_prefix(style_ctx)
    lock = subject_prompt(style_ctx)
    constraints = shot_constraints(shot or {"order": index})
    parts = [part for part in (prefix, lock, f"本镜画面：{body}" if body else "", constraints) if part]
    return "。".join(parts)


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
    """一次任务的完整视觉契约：主体、参考图、提示词、种子、负向词和 grade。"""
    style_ctx = style_ctx or {}
    items = list(shots) if shots else [{"order": order} for order in range(1, SHOT_COUNT + 1)]
    prefix = style_prefix(style_ctx)
    negative = negative_prompt(style_ctx)
    lock = subject_lock(style_ctx)
    planned = []
    for index, shot in enumerate(items, start=1):
        visual = str(shot.get("visual_prompt") or "")
        planned.append(
            {
                "order": shot_order(shot, index),
                "seed": seed_for_shot(task_id, shot, style_ctx, index),
                "prompt": compose_prompt(visual, style_ctx, shot=shot, index=index),
                "negative_prompt": negative,
                "style_prefix": prefix,
                "subject_id": lock["id"],
                "subject_prompt": subject_prompt(style_ctx),
                "constraints": shot_constraints(shot),
                "reference_sha256": lock["reference_sha256"],
            }
        )
    return {
        "task_id": str(task_id or ""),
        "prompt_version": PROMPT_VERSION,
        "seed_base": task_seed_base(task_id, style_ctx),
        "style_prefix": prefix,
        "negative_prompt": negative,
        "subject_lock": lock,
        "grade": grade(style_ctx),
        "shots": planned,
    }


def validate_plan(value: dict[str, Any]) -> dict[str, Any]:
    """对生成前契约做确定性验收，不把“看起来像”冒充主体一致。

    返回结构化结果，便于写进任务记录和离线报告。它验证主体描述、参考图哈希、
    风格词、负向词和 seed 基点是否真的被逐镜复用，但不评价像素是否好看；
    像素质量由 services.visual_qa 另行测量。
    """
    lock = value.get("subject_lock") if isinstance(value.get("subject_lock"), dict) else {}
    subject_id = str(lock.get("id") or "")
    reference_sha = str(lock.get("reference_sha256") or "")
    prefix = str(value.get("style_prefix") or "")
    negative = str(value.get("negative_prompt") or "")
    failures: list[str] = []
    shots = value.get("shots") if isinstance(value.get("shots"), list) else []
    if not subject_id:
        failures.append("缺少主体锁定 ID")
    if bool(lock.get("required")) and not reference_sha:
        failures.append("已要求主体锁定但缺少参考图；只写主体描述仍是 prompt 约束，不是图像条件")
    if not shots:
        failures.append("没有镜头可验收")
    seen_visuals: set[str] = set()
    for item in shots:
        order = item.get("order", "?")
        if str(item.get("subject_id") or "") != subject_id:
            failures.append(f"镜头 {order} 使用了不同主体 ID")
        if str(item.get("reference_sha256") or "") != reference_sha:
            failures.append(f"镜头 {order} 的参考图哈希不一致")
        if str(item.get("style_prefix") or "") != prefix:
            failures.append(f"镜头 {order} 的风格前缀不一致")
        if str(item.get("negative_prompt") or "") != negative:
            failures.append(f"镜头 {order} 的负向词不一致")
        prompt = str(item.get("prompt") or "")
        if subject_id and subject_id not in prompt:
            failures.append(f"镜头 {order} 的 prompt 未包含主体 ID")
        visual = prompt.rsplit("本镜画面：", 1)[-1].split("。生成约束：", 1)[0]
        seen_visuals.add(visual)
    if len(seen_visuals) < min(2, len(shots)):
        failures.append("镜头画面描述没有形成有效变化")
    return {
        "version": SUBJECT_LOCK_VERSION,
        "passed": not failures,
        "subject_id": subject_id,
        "reference_sha256": reference_sha,
        "subject_conditioning": "reference-image" if reference_sha else "prompt-only",
        "shot_count": len(shots),
        "anchored_shot_count": sum(
            1 for item in shots if str(item.get("subject_id") or "") == subject_id
        ),
        "failures": failures,
    }
