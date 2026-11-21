# -*- coding: utf-8 -*-
"""知识注入效果评测：以"可核实的具体性"为指标，对比注入开关。

为什么不用"年份事实上屏数"做指标：实测发现 DeepSeek 更倾向用意象和工艺名
体现具体性（如"茶筅击拂点茶""茶沫细腻如雪"），而非倾倒数字年份——这是更好
的写法，也符合参考块第 2 条规则的引导。用年份计数会低估注入效果。

指标改为三档，从宽到严：
  · 实体命中   —— 知识库实体名/别名出现在文案或画面描述里
  · 具体名词   —— 知识库 facts/imagery 中出现的专有名词（塔名、工艺名、
                  器物名、地名）被用上
  · 事实性表述 —— 带时间锚点或数量信息的表述（年份、朝代、层数、公里数）

用法：
    python scripts/kb_injection_eval.py --runs 4
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import settings  # noqa: E402
from backend.app.services import knowledge  # noqa: E402

SCHEMA = (
    '{"title":"string","summary":"string","cta":"string","tags":["string"],'
    '"narration":"string","shots":[{"id":"shot_1","order":1,"duration_sec":5,'
    '"title":"string","visual_prompt":"string","narration":"string",'
    '"subtitle":"string","transition":"fade"}]}'
)

# 事实性表述：年份（阿拉伯/中文纪年）、朝代、带单位的数量
FACT_RE = re.compile(
    r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)"
    r"|[元天启康熙乾隆道光咸丰同治光绪宣和淳熙嘉定][元二三四五六七八九十]+年"
    r"|(?:北宋|南宋|唐代|宋代|明朝|清代|元代|唐代)"
    r"|[一二三四五六七八九十百千万]+(?:年|月|公里|米|层|座|项|首|处|里)"
)
SIZE_RE = re.compile(r"\d+\s*[xX×]\s*\d+")


def _proper_nouns() -> set[str]:
    """从知识库 facts/imagery 中提取专有名词作为"具体性"词表。

    做法：把 facts/imagery 切成 2-6 字的片段，保留在语料中出现、但不在
    通用词表（的/了/在/杭州/西湖 等）里的实词。用于判断生成内容是否真的
    用上了资料里的具体信息。
    """
    stop = {
        "杭州", "西湖", "中国", "世界", "遗产", "名录", "文化", "历史", "景观",
        "景区", "城市", "特色", "代表", "重要", "著名", "位于", "以及", "其中",
        "也是", "成为", "拥有", "面积", "全长", "距今", "已经", "位于", "一带",
    }
    nouns: set[str] = set()
    # 手工维护的高价值具体名词来源——直接来自知识库实体字段，
    # 比自动切词更可靠（自动切词会把"中国首个湖泊"这类短语也收进来）
    for entity in knowledge._entities():
        for text in [
            str(entity.get("name") or ""),
            *(entity.get("facts") or []),
            *(entity.get("imagery") or []),
            str(entity.get("micro_drama_hook") or ""),
        ]:
            for token in re.findall(r"[\u4e00-\u9fff]{2,6}", text):
                if token in stop or len(token) < 2:
                    continue
                nouns.add(token)
    return nouns


def measure(result: dict, entity_names: set[str], facts_vocab: set[str]) -> dict:
    text = SIZE_RE.sub(
        " ",
        " ".join(
            [
                str(result.get("title", "")),
                str(result.get("summary", "")),
                str(result.get("narration", "")),
                *[str(s.get("narration", "")) for s in result.get("shots") or []],
                *[str(s.get("visual_prompt", "")) for s in result.get("shots") or []],
            ]
        ),
    )
    entities = sorted({n for n in entity_names if len(n) >= 2 and n in text})
    # 具体名词：词表里命中，且不是实体名本身（避免与实体指标重复计数）
    concrete = sorted(
        {n for n in facts_vocab if len(n) >= 3 and n in text and n not in entity_names}
    )
    facts = FACT_RE.findall(text)
    return {
        "entity_count": len(entities),
        "entities": entities,
        "concrete_count": len(concrete),
        "concrete": concrete[:12],
        "fact_count": len(facts),
        "facts": sorted(set(facts)),
    }


def build_prompt(context: dict, block: str) -> str:
    duration = int(context.get("duration", 30))
    shot_count = int(context.get("shot_count") or 6)
    rule = (
        "不得编造实时价格、活动日期、交通班次、荣誉或其他无法核验的事实。"
        if not block
        else "实时价格、活动日期、交通班次、荣誉等资料未覆盖的信息不得编造。"
    )
    return (
        "你是浙江文旅竖屏短视频总编导。请根据用户输入生成可直接用于视频制作的 JSON。"
        f"必须生成 {shot_count} 个镜头，镜头时长总和必须等于 {duration} 秒。"
        "每个镜头必须有具体可视化画面、旁白和字幕，不能使用空字符串、占位符或泛泛的“风景”。"
        f"{rule}{block}"
        "只输出 JSON，不要 Markdown，不要解释。JSON 结构必须符合："
        f"{SCHEMA}用户输入：{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
    )


def call_llm(instruction: str) -> dict:
    body = {
        "model": settings.llm_model,
        "temperature": 0.7,
        "messages": [
            {"role": "system", "content": "你是严谨的浙江文旅视频编导，只输出合法 JSON。"},
            {"role": "user", "content": instruction},
        ],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.llm_api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=settings.llm_timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return json.loads(payload["choices"][0]["message"]["content"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--city", default="杭州")
    parser.add_argument("--landmark", default="西湖")
    parser.add_argument("--theme", default="春日城市漫游")
    parser.add_argument("--culture", default="宋韵文化")
    parser.add_argument("--festival", default="春季文旅推广")
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    if not settings.llm_base_url or not settings.llm_api_key:
        print("ERROR: LLM_BASE_URL / LLM_API_KEY 未配置")
        return 1

    context = {
        "city": args.city, "landmark": args.landmark, "theme": args.theme,
        "culture": args.culture, "festival": args.festival,
        "audience": "年轻游客", "duration": 30, "style": "诗意纪实", "shot_count": 6,
    }
    block = knowledge.build_generation_context(
        city=args.city, landmark=args.landmark, theme=args.theme,
        culture=args.culture, festival=args.festival,
    )
    entity_names: set[str] = set()
    for entity in knowledge._entities():
        entity_names.add(str(entity.get("name") or ""))
        entity_names.update(str(a) for a in (entity.get("aliases") or []))
    facts_vocab = _proper_nouns()

    print(f"模型 {settings.llm_model} | 参考块 {len(block)} 字 | 具体名词词表 {len(facts_vocab)} 个 | 每组 {args.runs} 次\n")

    report: dict = {"context": context, "runs": args.runs, "groups": {}}
    for label, kw in (("A_无注入", ""), ("B_有注入", block)):
        samples = []
        print(f"→ {label}")
        for index in range(args.runs):
            try:
                stats = measure(call_llm(build_prompt(context, kw)), entity_names, facts_vocab)
            except Exception as exc:  # noqa: BLE001
                print(f"  第{index + 1}次失败: {exc}")
                continue
            samples.append(stats)
            print(
                f"  第{index + 1}次: 实体 {stats['entity_count']} | "
                f"具体名词 {stats['concrete_count']} {stats['concrete'][:5]}"
            )
        if samples:
            report["groups"][label] = {
                "samples": samples,
                "mean_entity": round(statistics.mean(s["entity_count"] for s in samples), 2),
                "mean_concrete": round(statistics.mean(s["concrete_count"] for s in samples), 2),
                "mean_fact": round(statistics.mean(s["fact_count"] for s in samples), 2),
            }
        print()

    if len(report["groups"]) == 2:
        a, b = report["groups"]["A_无注入"], report["groups"]["B_有注入"]
        print("=" * 72)
        print(f"{'指标':<18}{'A 无注入':>14}{'B 有注入':>14}{'变化':>16}")
        for key, name in (
            ("mean_entity", "知识实体命中"),
            ("mean_concrete", "具体名词数"),
            ("mean_fact", "事实性表述"),
        ):
            delta = b[key] - a[key]
            flag = f"+{delta:.2f}" if delta > 0 else f"{delta:.2f}"
            print(f"{name:<18}{a[key]:>14}{b[key]:>14}{flag:>16}")
        print("=" * 72)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"明细写入 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
