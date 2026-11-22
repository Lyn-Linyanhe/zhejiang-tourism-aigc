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
import re
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
    blob = " ".join(str(t) for t in texts if t).casefold()
    if not blob:
        return 0
    best = 0
    for needle in (entity.get("name"), *(entity.get("aliases") or [])):
        needle = str(needle or "").strip().casefold()
        if len(needle) >= 2 and len(needle) > best and needle in blob:
            best = len(needle)
    return best


def canonical_city(city: str) -> str:
    return str(city or "").strip().removesuffix("市")


def _city_entities(city: str) -> list[dict]:
    city = canonical_city(city)
    return [entity for entity in _entities() if not city or entity.get("city") == city]


def list_entities(city: str = "") -> list[dict]:
    """Entity references available for manual shot and library selection."""
    return [{key: entity[key] for key in ("id", "name", "city", "type")} for entity in _city_entities(city)]


def get_entity(entity_id: str, city: str = "") -> dict | None:
    return next((entity for entity in _city_entities(city) if entity["id"] == entity_id), None)


def _tag_match(entity: dict, *texts: str) -> int:
    blob = " ".join(text.casefold() for text in texts if text)
    return max((len(tag) for tag in entity.get("scenario_tags", []) if len(tag) >= 2 and tag.casefold() in blob), default=0)


def resolve_entity(*texts: str, city: str = "", allow_tags: bool = True) -> dict | None:
    """Use the most specific unambiguous match in the selected city."""
    pool = _city_entities(city)
    for matcher in (_match_text, _tag_match) if allow_tags else (_match_text,):
        hits = [(matcher(entity, *texts), entity) for entity in pool]
        best = max((score for score, _ in hits), default=0)
        if best:
            top = [entity for score, entity in hits if score == best]
            return top[0] if len(top) == 1 else None
    return None


def _type_label(entity_type: str) -> str:
    return {
        "scenic": "景区/景观",
        "intangible": "非遗项目",
        "cuisine": "地方饮食",
        "festival": "节庆活动",
        "legend": "传说故事",
        "person": "历史人物",
    }.get(str(entity_type or "").strip(), "文旅条目")


_PUNCT_RE = re.compile(r"[\s，。；、：（）()《》“”\"'·—\-–~!？?,.;:]+")


def _normalize_fact(text: object) -> str:
    """Normalize a fact for matching, without changing what gets displayed.

    Facts and records are often paraphrases of each other ("始建于东晋咸和元年
    （326 年）" vs "公元326年，灵隐寺在杭州始建"), and punctuation/spacing drift
    between edits. Exact string equality therefore reports "no source" for
    facts that do have one. Stripping punctuation and whitespace is a cheap,
    predictable step; anything looser would start matching unrelated facts.
    """
    return _PUNCT_RE.sub("", str(text or "")).lower()


def _evidence_status(entity: dict, facts: list[str]) -> str:
    """Describe how many injected facts actually have per-fact source records.

    Records may bind to a fact two ways:
    - `fact_index`: explicit 0-based position, used when the record text is a
      paraphrase and cannot be matched by string equality;
    - `text`: matched after punctuation/whitespace normalization.
    Explicit binding wins, because it survives rewording on either side.
    """
    selected = [str(f).strip() for f in facts[:FACTS_PER_ENTITY] if str(f).strip()]
    if not selected:
        return ""
    raw_records = [r for r in (entity.get("fact_records") or []) if isinstance(r, dict)]
    by_index = {}
    by_text = {}
    for record in raw_records:
        index = record.get("fact_index")
        if isinstance(index, int) and not isinstance(index, bool):
            by_index.setdefault(index, record)
        by_text[_normalize_fact(record.get("text"))] = record
    matched = []
    for position, text in enumerate(selected):
        record = by_index.get(position) or by_text.get(_normalize_fact(text))
        matched.append(record)
    count = sum(1 for record in matched if record)
    if count == len(selected):
        pending = any(_license_pending(record.get("license_status")) for record in matched if record)
        return f"逐条来源已登记（{count}/{len(selected)}）" + ("，许可待核" if pending else "")
    if count:
        return f"部分事实有来源记录（{count}/{len(selected)}），其余仅作背景"
    return "尚未逐条登记来源，仅作背景参考"


def _license_pending(status: object) -> bool:
    """Treat a license as pending unless it is an explicit clearance.

    Free-text matching on words like "review" or "未" is unreliable: "preview"
    contains "review", and a status can be "已确认可复用" while still mentioning
    no pending work. Only an explicit clearance list counts as cleared.
    """
    text = str(status or "").strip().lower()
    if not text:
        return True
    if text in {"approved", "cleared", "public-domain", "cc0"}:
        return False
    if "approved" in text or "已获授权" in text or "可公开复用" in text:
        return False
    return True


def _status_rank(status: str) -> int:
    """0 = fully sourced, 1 = partially, 2 = background only, 3 = unknown."""
    if status.startswith("逐条来源已登记"):
        return 0
    if status.startswith("部分事实有来源记录"):
        return 1
    if status.startswith("尚未逐条登记来源"):
        return 2
    return 3


def _fact_rule(statuses: list[str], fact_count: int) -> str:
    """Pick a fact-usage rule that matches how well the injected facts are sourced.

    The old text always demanded "at least 3 facts in the narration", which
    contradicted the header's "background only" warning and pushed the model to
    treat unverified material as citable. It also asked for more facts than some
    entities actually carry, which invites fabrication. The rule now scales with
    both evidence quality and the number of facts really injected.
    """
    if not statuses:
        return ""
    worst = max(_status_rank(status) for status in statuses)
    if worst == 0:
        want = min(fact_count, 3)
        return (
            f"1. 上述资料中的具体事实应真正用进旁白：挑 {want} 条写进不同镜头的旁白，"
            "让观众听到可核实的细节（年代、人物、工艺名、典故、具体地名），而不是泛泛的赞美。\n"
        )
    if worst == 1:
        return (
            "1. 只把「来源状态」为逐条登记的事实写进旁白；标为“其余仅作背景”的内容"
            "只能提供氛围与用词，不得当作可核实事实陈述。\n"
        )
    return (
        "1. 上述内容仅作背景与用词参考，不要写进旁白当作可核实事实；"
        "旁白请依靠画面描述与通用文化常识，不要补编年代、数字、荣誉称号。\n"
    )


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

    city = canonical_city(city)
    landmark = _clean(landmark)
    theme = _clean(theme)
    culture = _clean(culture)
    festival = _clean(festival)

    # 城市过滤同时用于脚本和画面，不跨城填充未覆盖的条目。
    pool = _city_entities(city)
    scored: list[tuple[float, dict]] = []
    for entity in pool:
        score = max((
            weight + hit / (hit + 10)
            for weight, text in ((4, landmark), (3, culture), (2, festival), (1, theme))
            if (hit := _match_text(entity, text))
        ), default=0.0)
        if not score:
            hit = _tag_match(entity, landmark, culture, festival, theme)
            if hit:
                score = hit / (hit + 10)
        if score:
            scored.append((score, entity))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("name", ""))))

    meta = _city_meta(city) if city else {}
    city_dna = str(meta.get("city_dna") or "").strip()

    if scored:
        blocks: list[str] = []
        statuses: list[str] = []
        injected_facts = 0
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
            evidence = _evidence_status(entity, facts)
            if evidence:
                lines.append(f"- 来源状态：{evidence}")
            block = "\n".join(lines)
            if used + len(block) > max_chars and blocks:
                break
            blocks.append(block)
            # Only entities that actually made it into the block may influence
            # the header; a budget-dropped entity's status would otherwise lie.
            if evidence:
                statuses.append(evidence)
            injected_facts += len(facts[:FACTS_PER_ENTITY])
            used += len(block)
        all_sourced = bool(statuses) and all(item.startswith("逐条来源已登记") for item in statuses)
        if all_sourced:
            header = f"以下是{city or '浙江'}的浙江文旅资料（事实逐条登记来源；许可状态见条目，引用前仍需核对）。"
        else:
            header = f"以下是{city or '浙江'}的浙江文旅资料（来源登记状态见条目；未逐条登记来源的内容仅作背景，不要当作已核实事实引用）。"
        body = "\n\n".join(blocks)
    else:
        # 未命中具体实体：至少给出城市气质与一个兜底实体，避免参考块为空。
        # 池为空（该城市尚无语料）时不硬凑事实，只交出使用规则供调用方降级判断。
        fallback = next(
            (e for e in pool if e.get("type") == "scenic"),
            pool[0] if pool else None,
        )
        lines = []
        statuses = []
        injected_facts = 0
        if city_dna:
            lines.append(f"- 城市气质：{city_dna}")
        if fallback is not None:
            lines.append(f"- 代表性条目：{fallback.get('name')}")
            facts = [str(f).strip() for f in (fallback.get("facts") or []) if str(f).strip()]
            if facts:
                lines.append("- 可用事实：" + "；".join(facts[:FACTS_PER_ENTITY]))
                injected_facts = len(facts[:FACTS_PER_ENTITY])
            evidence = _evidence_status(fallback, facts)
            if evidence:
                lines.append(f"- 来源状态：{evidence}")
                statuses.append(evidence)
        if not lines:
            return ""
        if statuses and all(item.startswith("逐条来源已登记") for item in statuses):
            header = f"以下是{city or '浙江'}的文旅背景资料（事实已逐条登记来源；许可状态见条目）。"
        else:
            header = f"以下是{city or '浙江'}的文旅背景资料（来源状态见条目；未登记来源的内容仅作背景）。"
        body = "\n".join(lines)

    fact_rule = _fact_rule(statuses, injected_facts)

    return (
        f"{header}\n\n{body}\n\n"
        "使用规则（必须遵守）：\n"
        f"{fact_rule}"
        "2. 事实要拆散到镜头里，一条事实配一个画面，不要集中在一句话里罗列。\n"
        "3. 上述资料未覆盖的信息（实时票价、活动日期、交通班次、荣誉称号）一律不要编造。\n"
        "4. 画面描述可参考「画面提示」，它对应真实存在的景观细节。\n"
        "5. 来源或许可状态不明确的内容只能作背景，不得改写成“已核实”“已批准”或具体荣誉。"
    )


def english_query(*texts: str, city: str = "", landmark: str = "", entity_id: str = "") -> str:
    """Return search terms for the named entity within the selected city."""
    if entity_id:
        entity = get_entity(entity_id, city)
    else:
        entity = resolve_entity(*texts, city=city)
        # Only an explicitly mentioned, known landmark can supply a fallback.
        if entity is None and landmark and any(landmark in text for text in texts if text):
            entity = resolve_entity(landmark, city=city, allow_tags=False)
    keywords = (entity or {}).get("keywords_en") or []
    return "; ".join(str(term).strip() for term in keywords[:3] if str(term).strip())
