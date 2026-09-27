"""音乐 provider —— 背景音乐匹配与生成。

从原 media.py 拆出，归属声音线（P3）。

按 models.ALLOWED_MUSIC_MOODS（舒缓 / 热烈 / 雅致 / 轻快）匹配曲库情绪清单，
同类别内随机选曲。清单缺失或读失败时回退到「目录里第一个」。
"""

from __future__ import annotations

import json
import random
from pathlib import Path


SUPPORTED_MUSIC_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
)

# music_mood（中文枚举）→ 曲库情绪类别（mood_manifest.json 的 category）。
MOOD_CATEGORY = {
    "舒缓": "calm",
    "热烈": "grand",
    "雅致": "ancient",
    "轻快": "poetic",
}


def find_music_file(music_dir: Path, mood: str = "") -> Path | None:
    """按情绪选择背景音乐。

    优先级：
    1. 曲库情绪清单（mood_manifest.json）里与 music_mood 对应类别的曲目，
       同类别内随机选一首；
    2. 文件名含 mood 关键词的文件；
    3. 回退目录里第一个音乐文件（音乐库为空仍返回 None）。

    mood 缺省时与旧行为一致：取目录里第一个文件。
    """
    if not music_dir.exists():
        return None
    files = [
        path
        for path in sorted(music_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_MUSIC_SUFFIXES
    ]
    if not files:
        return None
    mood_needle = str(mood or "").strip()

    manifest_path = music_dir / "mood_manifest.json"
    category = MOOD_CATEGORY.get(mood_needle, "")
    if manifest_path.exists() and category:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidates = [
                path
                for path in files
                if manifest.get(path.name, {}).get("category") == category
            ]
            if candidates:
                return random.choice(candidates)
        except (json.JSONDecodeError, OSError):
            pass

    if mood_needle:
        tagged = [path for path in files if mood_needle in path.stem]
        if tagged:
            return tagged[0]

    return next(iter(files), None)


# 旁白在说话时，渲染线应把 BGM 压到这个比例。
# 不改 renderer.py 的 amix；建议只写进 AudioResult.event，由 P4 落地。
DUCKED_MUSIC_VOLUME = 0.18
DUCK_RELEASE_SEC = 0.35


def select_music(music_dir: Path, mood: str) -> dict[str, object]:
    """按情绪选曲，并带上给渲染线的压低建议。

    选曲本身仍走 find_music_file：舒缓/热烈/雅致/轻快对应
    mood_manifest.json 的 calm/grand/ancient/poetic，同类别内随机。
    """
    selected = find_music_file(Path(music_dir), mood)
    return {
        "path": str(selected) if selected else "",
        "mood": str(mood or "").strip(),
        "category": MOOD_CATEGORY.get(str(mood or "").strip(), ""),
        "provider": "local-music-library" if selected else "none",
        "duck": {
            "when": "narration",
            "music_volume": DUCKED_MUSIC_VOLUME,
            "release_sec": DUCK_RELEASE_SEC,
            "note": "旁白说话时压低背景音乐。滤镜由渲染线改，声音线不改 renderer.py。",
        },
    }
