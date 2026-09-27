from __future__ import annotations

import hashlib
from typing import Any

from .llm import LLMProvider, LLMResult


CITY_KNOWLEDGE: dict[str, dict[str, Any]] = {
    "杭州": {
        "landmark": "西湖",
        "aliases": ["灵隐寺", "京杭大运河", "良渚古城遗址公园"],
        "culture": "宋韵文化、茶文化、数字经济城市气质",
        "color": "#1f6f68",
        "accent": "#d6ad60",
        "fact": "西湖文化景观于 2011 年列入世界文化遗产名录",
    },
    "宁波": {
        "landmark": "天一阁",
        "aliases": ["东钱湖", "象山影视城", "三江口"],
        "culture": "海丝文化、藏书文化、港口城市精神",
        "color": "#24526a",
        "accent": "#d8a45b",
        "fact": "天一阁是中国现存历史最悠久的私家藏书楼之一",
    },
    "温州": {
        "landmark": "雁荡山",
        "aliases": ["江心屿", "楠溪江", "泰顺廊桥"],
        "culture": "山水诗路、商贸文化、南戏传统",
        "color": "#315d4d",
        "accent": "#e5b866",
        "fact": "雁荡山以奇峰、飞瀑、洞壑和凝灰岩地貌闻名",
    },
    "绍兴": {
        "landmark": "鲁迅故里",
        "aliases": ["兰亭", "沈园", "东湖"],
        "culture": "越文化、书法文化、黄酒文化",
        "color": "#594a42",
        "accent": "#c98d54",
        "fact": "绍兴黄酒、兰亭书法和水乡街巷共同构成城市记忆",
    },
    "湖州": {
        "landmark": "南浔古镇",
        "aliases": ["莫干山", "太湖", "丝绸小镇"],
        "culture": "江南水乡、湖笔文化、生态文明",
        "color": "#2d6c63",
        "accent": "#e4bc70",
        "fact": "湖州是中国著名的湖笔产地，也是江南水乡的重要代表",
    },
    "嘉兴": {
        "landmark": "乌镇",
        "aliases": ["西塘古镇", "南湖", "月河历史街区"],
        "culture": "江南水乡、红船精神、古镇生活美学",
        "color": "#3c5f65",
        "accent": "#d5a861",
        "fact": "乌镇以河网、桥梁、白墙黛瓦和传统工艺构成独特水乡景观",
    },
    "金华": {
        "landmark": "婺州古城",
        "aliases": ["横店影视城", "双龙洞", "浦江水晶"],
        "culture": "婺剧、火腿饮食、影视文创",
        "color": "#654d3e",
        "accent": "#d4a25b",
        "fact": "婺剧是浙江重要地方戏曲，唱腔和脸谱具有鲜明地域特色",
    },
    "衢州": {
        "landmark": "江郎山",
        "aliases": ["衢州古城", "廿八都古镇", "孔氏南宗家庙"],
        "culture": "南孔文化、古道文化、山水生态",
        "color": "#35634e",
        "accent": "#e4b96d",
        "fact": "江郎山以三爿石奇峰和丹霞地貌景观著称",
    },
    "舟山": {
        "landmark": "普陀山",
        "aliases": ["东极岛", "嵊泗列岛", "朱家尖"],
        "culture": "海洋文化、观音文化、渔乡生活",
        "color": "#1c6072",
        "accent": "#f0c56c",
        "fact": "舟山群岛拥有海岛、渔港、寺院和海上日出的复合景观",
    },
    "台州": {
        "landmark": "神仙居",
        "aliases": ["府城墙", "天台山", "大陈岛"],
        "culture": "山海经纬、和合文化、海防记忆",
        "color": "#3e6550",
        "accent": "#e4b76b",
        "fact": "神仙居以火山流纹岩峰林、云海和峡谷景观见长",
    },
    "丽水": {
        "landmark": "古堰画乡",
        "aliases": ["云和梯田", "缙云仙都", "南尖岩"],
        "culture": "生态文明、畲族文化、乡村艺术",
        "color": "#477052",
        "accent": "#e6c274",
        "fact": "丽水以高森林覆盖率、梯田、古村和山水画乡构成生态名片",
    },
}


class OfflineLLMProvider(LLMProvider):
    name = "offline-zhejiang-editor"
    model = "zhejiang-editor-v1"

    def generate(self, prompt: str, context: dict[str, Any]) -> LLMResult:
        city = context["city"]
        knowledge = CITY_KNOWLEDGE.get(city, CITY_KNOWLEDGE["杭州"])
        landmark = context.get("landmark") or knowledge["landmark"]
        theme = context["theme"]
        culture = context.get("culture") or knowledge["culture"]
        festival = context.get("festival") or "当季文旅推广"
        audience = context.get("audience") or "年轻游客"
        duration = int(context.get("duration", 30))
        style = context.get("style", "诗意纪实")
        shot_count = int(context.get("shot_count") or (4 if duration <= 15 else 6 if duration <= 30 else 8))
        total_unit = duration / shot_count
        hooks = [
            f"如果把一座城市写成一首诗，{city}的第一句，往往从{landmark}开始。",
            f"沿着水脉与山色走进{city}，你会遇见{culture}留下的时间纹理。",
            f"这里不只是一处风景，更是一场属于{audience}的城市发现。",
        ]
        title = f"{city}，把{theme}写进山水之间"
        summary = f"围绕{theme}，以{landmark}为视觉入口，讲述{city}的{culture}与当季{festival}。"
        narration = (
            f"{hooks[0]} "
            f"清晨的光落在{landmark}，古老的街巷、桥影和远山慢慢苏醒。 "
            f"{knowledge['fact']}。 "
            f"从一杯茶、一味风物，到一段仍在延续的手艺，{city}把过去和今天放在同一条旅行路上。 "
            f"这个{festival}，来{city}走一走，让风景成为记忆，让每一次出发都值得被看见。"
        )
        visual_templates = [
            ("城市开场", f"{city}城市意象与{landmark}远景，晨雾、柔和日光、竖屏构图、{style}"),
            ("地标入画", f"{landmark}的代表性景观，水面或山体前景，游客剪影，镜头缓慢推进"),
            ("文化细节", f"{culture}相关的手作、街巷、器物或生活细节，特写，真实质感"),
            ("风物体验", f"{city}地方风物与市井烟火，摊铺、茶点、手艺，温暖自然光"),
            ("当季活动", f"{festival}氛围中的城市公共空间，灯笼、花事或人群，节奏明快"),
            ("收束号召", f"{city}山水与城市夜色交叠，留出标题空间，适合文旅宣传片结尾"),
            ("隐藏路线", f"{landmark}周边小众步道或古村，轻松旅行视角，空气感"),
            ("品牌收尾", f"{city}城市天际线与自然景观融合，电影感，适合品牌口号"),
        ]
        shots: list[dict[str, Any]] = []
        for index in range(shot_count):
            label, visual = visual_templates[index]
            start = round(index * len(narration) / shot_count)
            end = round((index + 1) * len(narration) / shot_count)
            text = narration[start:end].strip("，。 ")
            shots.append(
                {
                    "id": f"shot_{index + 1}",
                    "order": index + 1,
                    "duration_sec": round(total_unit, 2),
                    "title": label,
                    "visual_prompt": visual,
                    "narration": text + "。",
                    "subtitle": text + "。",
                    "transition": "fade" if index == 0 else ("dissolve" if index % 2 else "slideleft"),
                }
            )
        tags = [city, landmark, culture, festival, audience, style]
        data = {
            "title": title,
            "summary": summary,
            "cta": f"关注浙里成片，下一站，去{city}。",
            "tags": [tag for tag in tags if tag],
            "narration": narration,
            "shots": shots,
            "knowledge": {
                "fact": knowledge["fact"],
                "color": knowledge["color"],
                "accent": knowledge["accent"],
                "aliases": knowledge["aliases"],
            },
        }
        event = {
            "type": "llm",
            "provider": self.name,
            "model": self.model,
            "prompt_version": "zj-tourism-prompt-v1",
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "ok": True,
        }
        return LLMResult(data=data, provider=self.name, model=self.model, fallback_used=True, event=event)
