"""音乐 provider —— 背景音乐匹配与生成。

从原 media.py 拆出，归属声音线（P3）。

当前只有「从目录里捞第一个支持的音频文件」这一个能力，和情绪毫无关系。
要按 models.ALLOWED_MUSIC_MOODS（舒缓 / 热烈 / 雅致 / 轻快）做匹配或生成，
在这里扩展，不要改 orchestrator。
"""

from __future__ import annotations

from pathlib import Path


SUPPORTED_MUSIC_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"}
)


def find_music_file(music_dir: Path) -> Path | None:
    """从音乐目录里取第一个受支持的音频文件。

    注意：当前实现不看情绪、不看时长、不看版权状态，只是「目录里第一个」。
    这是已知的降级方案，正式实现见 02_分工/P3_声音线.md。
    """
    if not music_dir.exists():
        return None
    return next(
        (
            path
            for path in sorted(music_dir.iterdir())
            if path.is_file() and path.suffix.lower() in SUPPORTED_MUSIC_SUFFIXES
        ),
        None,
    )
