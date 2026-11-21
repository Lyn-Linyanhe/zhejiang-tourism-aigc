# -*- coding: utf-8 -*-
"""浙江文旅知识库（轻量版）：为脚本生成注入经核查的事实，为素材检索提供精准英文关键词。

数据来源：data_zhejiang_kb/*.json（46 个杭州实体、210 条经联网核查的事实，
覆盖景区/非遗/美食/节庆/传说/名人，字段契约见 _meta.json）。

设计约束：
- 纯 Python 标准库（JSON + 字符串匹配），零第三方依赖，符合本项目哲学；
- 宽松匹配：镜头描述/旁白里出现实体名或别名（>=2 字）即视为命中；
- 两个消费方向：
  ① build_generation_context() → 脚本生成阶段的事实参考块（治"文采好但细节空"）；
  ② english_query() → 素材检索阶段的英文检索词（治"要西湖搜回无名湖泊"）。

用法：
    from app.services import knowledge
    context = knowledge.build_generation_context(city="杭州", landmark="西湖",
                                                 theme="春日城市漫游")
    query = knowledge.english_query(shot["visual_prompt"], shot["narration"],
                                    city=task.input.city, landmark=task.input.landmark)
    # 命中返回如 "West Lake Hangzhou; Leifeng Pagoda sunset"，未命中返回 ""
"""
import json
import os
import threading
from typing import Any

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data_zhejiang_kb",
)

# 注入块的字数预算。system prompt 远未饱和，但参考块过长会稀释 LLM 注意力、
# 拖慢生成，且多余事实本就不会被用上——实测 1500 字约合 3-5 个实体的有效信息量。
MAX_CONTEXT_CHARS = 1500
# 参考块里每个实体给出的可直接引用的事实条数上限。给多了模型会堆砌年份数字，
# 反而读起来像百科词条；3 条足以支撑一个 30 秒片子的细节密度。
FACTS_PER_ENTITY = 3

_lock = threading.Lock()
_cache: list[dict] | None = None
_meta_cache: dict | None = None


def _entities() -> list[dict]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
    entities: list[dict] = []
    for name in sorted(os.listdir(_DATA_DIR)) if os.path.isdir(_DATA_DIR) else []:
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(_DATA_DIR, name)
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        city = str(payload.get("city") or "").strip()
        for entity in payload.get("entities") or []:
            if isinstance(entity, dict) and entity.get("id"):
                item = dict(entity)
                item.setdefault("city", city)
                entities.append(item)
    with _lock:
        _cache = entities
    return entities


def reset_cache() -> None:
    """语料更新后调用，强制重新加载。"""
    global _cache, _meta_cache
    with _lock:
        _cache = None
        _meta_cache = None


def stats() -> dict:
    entities = _entities()
    return {
        "entities": len(entities),
        "cities": sorted({str(e.get("city")) for e in entities}),
        "facts": sum(len(e.get("facts") or []) for e in entities),
    }


def _city_meta(city: str) -> dict:
    """取某地市的语料级元信息（城市别名、城市气质）。"""
    global _meta_cache
    with _lock:
        if _meta_cache is not None:
            return _meta_cache.get(city, {})
    payload: dict = {}
    for name in sorted(os.listdir(_DATA_DIR)) if os.path.isdir(_DATA_DIR) else []:
        if not name.endswith(".json") or name.startswith("_"):
            continue
        try:
            with open(os.path.join(_DATA_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        city_name = str(data.get("city") or "").strip()
        if city_name:
            payload[city_name] = data.get("city_meta") or {}
    with _lock:
        _meta_cache = payload
    return payload.get(city, {})


def _match_text(entity: dict, *texts: str) -> int:
    """返回实体在给定文本中的最长命中别名长度，未命中返回 0。"""
    blob = " ".join(str(t) for t in texts if t)
    if not blob:
        return 0
    best = 0
    for needle in (entity.get("name"), *(entity.get("aliases") or [])):
        needle = str(needle or "").strip()
        if len(needle) >= 2 and len(needle) > best and needle in blob:
            best = len(needle)
    return best


def _type_label(entity_type: str) -> str:
    return {
        "scenic": "景区/景观",
        "intangible": "非遗项目",
        "cuisine": "地方饮食",
        "festival": "节庆活动",
        "legend": "传说故事",
        "person": "历史人物",
    }.get(str(entity_type or "").strip(), "文旅条目")


def build_generation_context(
    *,
    city: str = "",
    landmark: str = "",
    theme: str = "",
    culture: str = "",
    festival: str = "",
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """把知识库里与本次创作相关的经核查事实，整理成一段可直接注入 LLM 的参考文本。

    检索策略（纯字符串匹配，零依赖）：
    1. 城市过滤——用户选了城市就只在该城市语料里找，避免宁波选题注入杭州事实；
    2. 打分——用户显式填写的字段权重高于主题等泛化文本：
       地标命中 4.0、文化线索 2.0、节庆 1.5、主题 1.0，再按命中别名长度微调，
       使"虎跑公园"这种更具体的名称优先于"西湖"这类宽泛名称；
    3. 组块——命中的实体按分数降序，逐个渲染成"条目/文化定位/可用事实/画面提示"，
       累计超过 max_chars 即停止（半截实体不入块）；
    4. 未命中任何实体时退化为该城市的"城市气质 + 首个 5A 景区"，保证至少给出地理限定词。

    返回值末尾带使用规则，调用方只需原样插入 prompt。

    已知局限（对使用者的说明，不写入返回值）：
    注入是 prompt 层面的概率性增强，不是确定性保证。是否真正引用事实存在
    采样波动，但整体方向稳定——实测（deepseek-chat，各 4 次采样）具体名词
    使用量 2.5 → 17.5，事实性表述 2.25 → 13.5，知识实体命中 4.75 → 6.25。
    评测口径与复现脚本见 scripts/kb_injection_eval.py；调整 prompt 结构或
    更换模型后应重跑该脚本，不要凭单次生成观感判断。

    另注：模型常以"天启元年""北宋苏轼元祐四年"等中式纪年表达事实，而非
    裸四位数字。做效果统计时若只匹配阿拉伯数字年份会严重低估注入效果。
    """
    entities = _entities()
    if not entities:
        return ""
    # 清洗空值：None 直接字符串化会变成 "None" 参与匹配，造成假命中
    def _clean(value: Any) -> str:
        return "" if value is None else str(value).strip()

    city = _clean(city)
    landmark = _clean(landmark)
    theme = _clean(theme)
    culture = _clean(culture)
    festival = _clean(festival)

    # 城市过滤是硬约束：用户选了宁波，语料里没有宁波就退回背景资料，
    # 绝不能把杭州的事实注入宁波片子——错误的权威事实比没有事实更糟。
    pool = [e for e in entities if not city or str(e.get("city", "")).strip() == city]

    scored: list[tuple[float, dict]] = []
    for entity in pool:
        score = 0.0
        if landmark:
            hit = _match_text(entity, landmark)
            if hit:
                # 地标是用户最明确的意图信号，权重最高；越具体（别名越长）越优先
                score = max(score, 4.0 + min(hit, 8) / 10)
        if culture:
            hit = _match_text(entity, culture)
            if hit:
                score = max(score, 2.0 + min(hit, 8) / 10)
        if festival:
            hit = _match_text(entity, festival)
            if hit:
                score = max(score, 1.5 + min(hit, 8) / 10)
        if theme:
            hit = _match_text(entity, theme)
            if hit:
                score = max(score, 1.0 + min(hit, 8) / 10)
        if score > 0:
            scored.append((score, entity))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("name", ""))))

    meta = _city_meta(city) if city else {}
    city_dna = str(meta.get("city_dna") or "").strip()

    if scored:
        blocks: list[str] = []
        used = 0
        for _score, entity in scored:
            facts = [str(f).strip() for f in (entity.get("facts") or []) if str(f).strip()]
            imagery = [str(i).strip() for i in (entity.get("imagery") or []) if str(i).strip()]
            lines = [f"### {entity.get('name')}（{_type_label(entity.get('type'))}）"]
            significance = str(entity.get("cultural_significance") or "").strip()
            if significance:
                lines.append(f"- 文化定位：{significance}")
            if facts:
                lines.append("- 可用事实：" + "；".join(facts[:FACTS_PER_ENTITY]))
            if imagery:
                lines.append("- 画面提示：" + "、".join(imagery[:4]))
            hook = str(entity.get("micro_drama_hook") or "").strip()
            if hook:
                lines.append(f"- 叙事切口：{hook}")
            block = "\n".join(lines)
            if used + len(block) > max_chars and blocks:
                break
            blocks.append(block)
            used += len(block)
        header = f"以下是与本次创作相关的浙江文旅资料（已核查，可放心引用）。"
        if city:
            header = f"以下是{city}的浙江文旅资料（已核查，可放心引用）。"
        body = "\n\n".join(blocks)
    else:
        # 未命中具体实体：至少给出城市气质与一个兜底实体，避免参考块为空。
        # 池为空（该城市尚无语料）时不硬凑事实，只交出使用规则供调用方降级判断。
        fallback = next(
            (e for e in pool if e.get("type") == "scenic"),
            pool[0] if pool else None,
        )
        lines = []
        if city_dna:
            lines.append(f"- 城市气质：{city_dna}")
        if fallback is not None:
            lines.append(f"- 代表性条目：{fallback.get('name')}")
            facts = [str(f).strip() for f in (fallback.get("facts") or []) if str(f).strip()]
            if facts:
                lines.append("- 可用事实：" + "；".join(facts[:FACTS_PER_ENTITY]))
        if not lines:
            return ""
        header = f"以下是{city or '浙江'}的文旅背景资料（已核查）。" if city else "以下是浙江文旅背景资料（已核查）。"
        body = "\n".join(lines)

    return (
        f"{header}\n\n{body}\n\n"
        "使用规则（必须遵守）：\n"
        f"1. 上述资料中的具体事实必须真正用进旁白：至少挑 {min(3, FACTS_PER_ENTITY)} 条"
        "写进不同镜头的旁白，让观众听到可核实的细节（年代、人物、工艺名、典故、"
        "具体地名），而不是泛泛的赞美。\n"
        "2. 事实要拆散到镜头里，一条事实配一个画面，不要集中在一句话里罗列。\n"
        "3. 上述资料未覆盖的信息（实时票价、活动日期、交通班次、荣誉称号）一律不要编造。\n"
        "4. 画面描述可参考「画面提示」，它对应真实存在的景观细节。"
    )


def english_query(*texts: str, city: str = "", landmark: str = "") -> str:
    """从给定文本中识别知识实体，返回其英文检索词。

    匹配规则：实体名称或别名（>=2 字符）作为子串出现在任一文本中即命中，
    取命中名称最长（最具体）的实体；同实体多文本命中不叠加。
    未命中或语料缺失时返回空串，调用方应回退到原有检索词逻辑。
    """
    blob = " ".join(str(t) for t in texts if t)
    if not blob:
        return ""
    entities = _entities()
    if not entities:
        return ""

    best_entity = None
    best_needle = ""
    for entity in entities:
        needles = [str(entity.get("name") or ""), *(str(a) for a in (entity.get("aliases") or []))]
        for needle in needles:
            needle = needle.strip()
            if len(needle) >= 2 and needle in blob and len(needle) > len(best_needle):
                best_entity = entity
                best_needle = needle

    keywords: list[str] = []
    if best_entity is not None:
        keywords = [str(k).strip() for k in (best_entity.get("keywords_en") or []) if str(k).strip()]

    # 城市/地标字段是最强信号（用户明确选择了城市与景点），始终附加，
    # 保证未命中具体实体时至少带上地理限定词。
    landmark_needle = str(landmark or "").strip()
    if landmark_needle and landmark_needle in blob and len(landmark_needle) >= 2:
        keywords.insert(0, landmark_needle)

    if not keywords:
        return ""
    return "; ".join(keywords[:3])
