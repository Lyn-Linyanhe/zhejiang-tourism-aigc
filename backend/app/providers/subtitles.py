"""中文旁白字幕的字体、语义换行和安全区；不改变 SRT 与原始 cue。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_FONT_CHOICES = (
    ("Microsoft YaHei", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("Microsoft YaHei", r"C:\Windows\Fonts\msyh.ttc"),
    ("PingFang SC", "/System/Library/Fonts/PingFang.ttc"),
    ("Noto Sans CJK SC", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("WenQuanYi Zen Hei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)
_CLOSING = set("，。！？；：、,.!?;:）)]》】」』”’％%")
_OPENING = set("（([《【「『“‘")
_TOKENS = re.compile(r"(?:公元)?[0-9]+(?:[.,:/-][0-9]+)*(?:[%％年月日秒米℃度])?|[A-Za-z]+(?:['’\-][A-Za-z]+)*|.", re.DOTALL)
_TIMING = re.compile(r"(\d+):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d+):(\d{2}):(\d{2}),(\d{3})")


@dataclass(frozen=True)
class CaptionStyle:
    width: int
    height: int
    font_name: str
    font_path: Path | None
    font_size: int
    margin_x: int
    margin_bottom: int
    outline: float
    shadow: float

    @property
    def line_width(self) -> int:
        return self.width - 2 * self.margin_x


def caption_style(width: int, height: int) -> CaptionStyle:
    if width <= 0 or height <= 0:
        raise ValueError("Subtitle canvas dimensions must be positive")
    family, path = next(((name, Path(file)) for name, file in _FONT_CHOICES if Path(file).is_file()), ("sans-serif", None))
    scale = min(width, height) / 720
    return CaptionStyle(
        width, height, family, path, max(12, round(44 * scale)),
        max(8, round(width * 0.08)),
        max(10, round(height * (0.12 if height > width else 0.08))),
        round(max(0.7, 1.5 * scale), 2), round(max(0.3, 0.6 * scale), 2),
    )


def _measure(text: str, size: int, font: Any = None) -> float:
    if font is not None:
        # libass 按字体的 ascender + descender 缩放，不能把字号当字面像素宽。
        ascent, descent = font.getmetrics()
        return float(font.getlength(text)) * size / max(1, ascent + descent)
    return size * sum(1.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 0.56 for char in text)


def _font(style: CaptionStyle, size: int) -> Any:
    if style.font_path is None:
        return None
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(str(style.font_path), size)
        try:
            font.set_variation_by_name("Regular")
        except (AttributeError, OSError, ValueError):
            pass
        return font
    except (ImportError, OSError):
        return None


def _breaks(tokens: list[str]) -> list[int]:
    return [index for index in range(1, len(tokens))
            if tokens[index][0] not in _CLOSING and tokens[index - 1][-1] not in _OPENING]


def layout_caption(text: str, style: CaptionStyle) -> tuple[list[str], int]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    if not text.strip():
        return [], style.font_size
    explicit = text.split("\n")
    if len(explicit) > 1:
        layouts = [layout_caption(line, style) for line in explicit]
        return [line for lines, _ in layouts for line in (lines or [""])], min(size for _, size in layouts)
    tokens = _TOKENS.findall(text)
    candidates = _breaks(tokens)
    minimum = max(10, round(style.font_size * 0.75))
    for size in range(style.font_size, minimum - 1, -1):
        font = _font(style, size)
        measure = lambda value: _measure(value, size, font)
        if len(explicit) <= 2 and all(measure(line) <= style.line_width for line in explicit):
            return explicit, size
        full = "".join(tokens)
        if measure(full) <= style.line_width:
            return [full], size
        fits = []
        for index in candidates:
            left = "".join(tokens[:index]).strip()
            right = "".join(tokens[index:]).strip()
            a, b = measure(left), measure(right)
            if max(a, b) > style.line_width:
                continue
            score = abs(a - b) / style.line_width
            if right.startswith(("的", "地", "得", "了", "着", "过")):
                score += 0.8
            if left[-1] in "，。！？；：,.!?;:":
                score -= 0.45
            elif right.startswith(("而", "也", "让", "与", "是")):
                score -= 0.12
            fits.append((score, left, right))
        if fits:
            _, left, right = min(fits, key=lambda item: item[0])
            return [left, right], size
    # 异常长的旧稿保留全文和可读字号，不能为了两行缩成蚂蚁字。
    size = minimum
    font = _font(style, size)
    tokens = [piece for token in tokens for piece in (list(token) if _measure(token, size, font) > style.line_width else [token])]
    lines = []
    while tokens:
        fits = [index for index in _breaks(tokens) if _measure("".join(tokens[:index]), size, font) <= style.line_width]
        if _measure("".join(tokens), size, font) <= style.line_width or not fits:
            lines.append("".join(tokens).strip())
            break
        index = max(fits)
        lines.append("".join(tokens[:index]).strip())
        tokens = tokens[index:]
    return lines, size


def _timestamp(parts: tuple[str, ...]) -> str:
    hours, minutes, seconds, millis = map(int, parts)
    total = ((hours * 60 + minutes) * 60 + seconds) * 100 + round(millis / 10)
    hours, rest = divmod(total, 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _escape_ass(text: str) -> str:
    # WORD JOINER 不显示字形，同时阻止字面反斜杠被当成 \\N 等控制符。
    return text.replace("\\", "\\\u2060").replace("{", r"\{").replace("}", r"\}")


def create_ass(srt_path: Path, output_path: Path, width: int, height: int) -> dict[str, Any]:
    style = caption_style(width, height)
    header = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{style.font_name},{style.font_size},&H00FAFAFA,&H00FAFAFA,&H30101010,&H80101010,-1,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},2,{style.margin_x},{style.margin_x},{style.margin_bottom},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events = []
    multiline = 0
    content = srt_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", content.strip()):
        rows = block.splitlines()
        if len(rows) < 3:
            continue
        timing = _TIMING.fullmatch(rows[1].strip())
        if not timing:
            continue
        lines, size = layout_caption("\n".join(rows[2:]), style)
        if not lines:
            continue
        multiline += len(lines) > 2
        text = r"\N".join(_escape_ass(line) for line in lines)
        size_tag = f"{{\\fs{size}}}" if size != style.font_size else ""
        events.append(f"Dialogue: 0,{_timestamp(timing.groups()[:4])},{_timestamp(timing.groups()[4:])},Caption,,0,0,0,,{size_tag}{text}")
    output_path.write_text("\n".join([*header, *events, ""]), encoding="utf-8-sig")
    return {
        "preset": "clean-documentary-v1", "font": style.font_name,
        "font_size_px": style.font_size, "canvas": f"{width}x{height}",
        "margin_x_px": style.margin_x, "margin_bottom_px": style.margin_bottom,
        "outline_px": style.outline, "shadow_px": style.shadow,
        "cue_count": len(events), "long_cue_count": multiline,
    }
