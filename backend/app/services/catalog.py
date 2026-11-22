from __future__ import annotations

from typing import Any

from ..providers.offline import CITY_KNOWLEDGE
from ..providers.tts import EDGE_VOICE_CHOICES
from . import knowledge as zhejiang_kb
from .voice_studio import STYLES, VOICE_LIBRARY
from ..providers.tianyi import availability as tianyi_availability


def get_catalog() -> dict[str, Any]:
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
        "tianyi": tianyi_availability(),
        "music_moods": ["舒缓", "热烈", "雅致", "轻快", "无音乐"],
        # 旁白出现时 BGM 的处理方式，默认不压低
        "music_ducking": ["不压低", "轻微", "标准"],
        # 时长是自由输入（5–180 秒），这里给的是常用快捷档，不是可选上限。
        "durations": [15, 30, 60],
        "duration_range": [5, 180],
        "duration_default": 15,
        "shot_count_range": [2, 12],
        "shot_count_default": 0,
        "resolutions": [
            {"value": "720x1280", "label": "竖屏 9:16 · 720P（抖音/小红书）"},
            {"value": "1080x1920", "label": "竖屏 9:16 · 1080P"},
            {"value": "1920x1080", "label": "横屏 16:9 · 1080P（B站/官网）"},
            {"value": "1080x1080", "label": "方形 1:1"},
        ],
        "festivals": ["春季文旅推广", "端午民俗体验", "暑期亲子旅行", "中秋团圆主题", "国庆城市漫游", "非遗主题周"],
        "audiences": ["年轻游客", "亲子家庭", "文化爱好者", "摄影旅行者", "城市品牌受众"],
    }


def get_music_library(catalog_path: str | Any = "", music_dir: str | Any = "") -> dict[str, Any]:
    """列出曲库里可用于选曲的曲目，供前端"浏览曲库"使用。

    只返回已授权（approved）的曲目；未授权的曲子不暴露给用户选择，
    避免选到不能进成片的素材。字段裁剪到 UI 需要的最小集。
    """
    import json
    from pathlib import Path

    from ..config import settings

    path = Path(catalog_path or settings.music_catalog_path)
    base = Path(music_dir or settings.music_dir)
    if not path.is_file():
        return {"tracks": [], "moods": [], "styles": [], "total": 0}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tracks": [], "moods": [], "styles": [], "total": 0}

    tracks: list[dict[str, Any]] = []
    moods: set[str] = set()
    styles: set[str] = set()
    for record in payload.get("tracks") or []:
        if not isinstance(record, dict) or not record.get("approved"):
            continue
        file_name = str(record.get("file") or "")
        if not file_name or not (base / file_name).is_file():
            continue
        track_moods = [str(m) for m in (record.get("moods") or [])]
        track_styles = [str(s) for s in (record.get("styles") or [])]
        moods.update(track_moods)
        styles.update(track_styles)
        tracks.append({
            "id": str(record.get("id") or file_name),
            "file": file_name,
            "name": Path(file_name).stem,
            "moods": track_moods,
            "styles": track_styles,
            "usage_scope": str(record.get("usage_scope") or ""),
            "moods_source": str(record.get("moods_source") or ""),
        })

    return {
        "tracks": tracks,
        "moods": sorted(moods),
        "styles": sorted(styles),
        "total": len(tracks),
    }


def resolve_music_track(file_name: str, catalog_path: str | Any = "", music_dir: str | Any = "") -> Any:
    """把曲库里的文件名解析成磁盘路径，只允许 catalog 里已授权曲目。

    试听接口靠这个把请求限制在曲库白名单内，避免用 `../` 之类的路径读到别处文件。
    """
    import json
    from pathlib import Path

    from ..config import settings

    name = Path(str(file_name or "")).name
    if not name:
        return None
    path = Path(catalog_path or settings.music_catalog_path)
    base = Path(music_dir or settings.music_dir).resolve()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for record in payload.get("tracks") or []:
        if not isinstance(record, dict) or not record.get("approved"):
            continue
        if Path(str(record.get("file") or "")).name != name:
            continue
        target = (base / name).resolve()
        if target.is_relative_to(base) and target.is_file():
            return target
        return None
    return None

