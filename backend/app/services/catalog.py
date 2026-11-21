from __future__ import annotations

from typing import Any

from ..providers.offline import CITY_KNOWLEDGE
from ..providers.tts import EDGE_VOICE_CHOICES
from .voice_studio import STYLES, VOICE_LIBRARY
import json
from ..config import settings


def bank_voices(config=settings):
    try:
        voices = json.loads(config.tts_voice_catalog)
    except (TypeError, ValueError):
        return {}
    return {key: label for key, label in voices.items()
            if isinstance(key, str) and 0 < len(key) <= 80 and isinstance(label, str) and label.strip()} if isinstance(voices, dict) else {}


def get_catalog(config=settings) -> dict[str, Any]:
    return {
        "cities": [
            {
                "name": city,
                "landmark": data["landmark"],
                "aliases": data["aliases"],
                "culture": data["culture"],
                "color": data["color"],
                "accent": data["accent"],
                "fact": data["fact"],
            }
            for city, data in CITY_KNOWLEDGE.items()
        ],
        "styles": ["诗意纪实", "城市漫游", "国风雅韵", "青春活力", "节庆热烈"],
        "voices": [*EDGE_VOICE_CHOICES, "无配音"],
        "voice_library": VOICE_LIBRARY,
        "voice_styles": STYLES,
        "bank_voices": bank_voices(config),
        "music_moods": ["舒缓", "热烈", "雅致", "轻快", "无音乐"],
        # 旁白出现时 BGM 的处理方式，默认不压低
        "music_ducking": ["不压低", "轻微", "标准"],
        "durations": [15, 30, 60],
        "festivals": ["春季文旅推广", "端午民俗体验", "暑期亲子旅行", "中秋团圆主题", "国庆城市漫游", "非遗主题周"],
        "audiences": ["年轻游客", "亲子家庭", "文化爱好者", "摄影旅行者", "城市品牌受众"],
    }
