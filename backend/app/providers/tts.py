from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class TTSResult:
    path: Path
    provider: str
    model: str
    event: dict[str, object]


class TTSProvider:
    name = "base"

    def synthesize(self, text: str, output_path: Path, voice: str) -> TTSResult:
        raise NotImplementedError


class OpenAICompatibleTTSProvider(TTSProvider):
    name = "openai-compatible-tts"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def synthesize(self, text: str, output_path: Path, voice: str) -> TTSResult:
        if not self.base_url or not self.api_key:
            raise RuntimeError("TTS provider 未配置 base_url 或 api_key")
        body = {"model": self.model, "input": text, "voice": voice, "response_format": "wav"}
        request = urllib.request.Request(
            f"{self.base_url}/audio/speech",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            output_path.write_bytes(response.read())
        return TTSResult(
            path=output_path,
            provider=self.name,
            model=self.model,
            event={"type": "tts", "provider": self.name, "model": self.model, "ok": True},
        )


# ---------------------------------------------------------------------------
# 函数式 TTS 实现（从原 media.py 拆出，归属声音线 P3）
#
# 上面的类式 provider 需要显式配置凭据；下面这两个是无凭据的兜底方案，
# 由 orchestrator 按顺序尝试。
#
# 注意：两者当前都只返回整段音频，不返回词级时间戳。要让字幕和画面真同步，
# 需要产出 cues（见 00_契约/CONTRACT.md 的 AudioResult）。
# ---------------------------------------------------------------------------


# 句末标点。词级时间戳按这些符号收成一句，不按总时长均分。
_SENTENCE_END = set("。！？!?；;")
_PAUSE = set("，、,：:")
_TICKS_PER_SECOND = 10_000_000


def voice_name_for(voice: str) -> str:
    """女声/男声映射到 edge-tts 声线；已经是声线名则原样返回。"""
    mapped = {
        "女声": "zh-CN-XiaoxiaoNeural",
        "男声": "zh-CN-YunxiNeural",
        "female": "zh-CN-XiaoxiaoNeural",
        "male": "zh-CN-YunxiNeural",
    }.get(str(voice or "").strip(), str(voice or "").strip())
    if not mapped:
        raise ValueError("edge TTS voice cannot be empty")
    return mapped


def rate_arg_for(rate: float) -> str:
    """1.0 → +0%。edge-tts 接受大约 -50% 到 +100%。"""
    percent = max(-50, min(100, round((float(rate) - 1.0) * 100)))
    return f"{percent:+d}%"


def probe_duration(path: Path) -> float:
    """用 ffprobe 读真实时长；没有 ffprobe 时再试 wav 头。读不到返回 0。"""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        measured = float((result.stdout or "").strip())
    except ValueError:
        measured = 0.0
    if measured > 0:
        return measured
    if path.suffix.lower() != ".wav":
        return 0.0
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            sample_rate = handle.getframerate()
    except (wave.Error, OSError):
        return 0.0
    if sample_rate <= 0:
        return 0.0
    return frames / sample_rate


def split_sentences(text: str) -> list[str]:
    """按句末标点切开。没有标点时整段算一句，不按字数均分。"""
    sentences: list[str] = []
    buf: list[str] = []
    for char in text.strip():
        buf.append(char)
        if char in _SENTENCE_END:
            piece = "".join(buf).strip()
            if piece:
                sentences.append(piece)
            buf = []
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return sentences


def _weight(text: str) -> int:
    """估算权重只计会出声的字，标点和空白不算。"""
    spoken = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return max(1, len(spoken))


def _snap(value: float, duration: float) -> float:
    snapped = min(duration, max(0.0, value))
    return round(snapped, 3)


def align_sentences(
    sentences: list[str],
    words: list[dict[str, Any]],
    duration: float,
) -> tuple[list[dict[str, float | str]], str]:
    """把词级时间戳收成句。

    词按顺序吃进句子。吃完后如果还有词没对上，或根本没有词，
    退回按标点比例估算，并标明 estimated。
    返回 (spans, timing)，timing 是 word 或 estimated。
    """
    if duration <= 0:
        raise ValueError("audio duration must be greater than 0")
    if not sentences:
        raise ValueError("speech text cannot be empty")

    consumed = 0
    spans: list[dict[str, float | str]] = []
    aligned = True
    for sentence in sentences:
        need = _weight(sentence)
        taken = 0
        chunk: list[dict[str, Any]] = []
        while consumed < len(words) and taken < need:
            word = words[consumed]
            chunk.append(word)
            consumed += 1
            taken += _weight(str(word.get("text", "")))
        if not chunk:
            aligned = False
            break
        spans.append(
            {
                "text": sentence,
                "start": float(chunk[0]["start"]),
                "end": float(chunk[-1]["end"]),
            }
        )
    # 词没用完，或某一句没有吃到词：时间对不齐，整段改估算。
    if not aligned or consumed < len(words) or len(spans) != len(sentences):
        spans = estimate_spans(sentences, duration)
        return spans, "estimated"
    spans = _clamp_spans(spans, duration)
    return spans, "word"


def estimate_spans(sentences: list[str], duration: float) -> list[dict[str, float | str]]:
    """拿不到词级时间戳时，按各句出声字数占整段的比例切真实时长。"""
    weights = [_weight(sentence) for sentence in sentences]
    total = sum(weights) or len(sentences)
    elapsed = 0.0
    spans: list[dict[str, float | str]] = []
    for index, sentence in enumerate(sentences):
        if index == len(sentences) - 1:
            end = duration
        else:
            end = elapsed + duration * weights[index] / total
        if end <= elapsed:
            end = min(duration, elapsed + 0.05)
        spans.append({"text": sentence, "start": elapsed, "end": end})
        elapsed = end
    return _clamp_spans(spans, duration)


def _clamp_spans(
    spans: list[dict[str, float | str]],
    duration: float,
) -> list[dict[str, float | str]]:
    """保证单调、不重叠、最后一句贴到音频结尾。"""
    if not spans:
        return []
    clamped: list[dict[str, float | str]] = []
    previous = 0.0
    last = len(spans) - 1
    for index, span in enumerate(spans):
        start = max(previous, float(span["start"]))
        end = duration if index == last else float(span["end"])
        end = min(duration, max(end, start + 0.05))
        if index == last:
            end = max(end, duration)
            end = max(end, start + 0.05)
        clamped.append(
            {
                "text": str(span["text"]),
                "start": _snap(start, end),
                "end": round(end, 3),
            }
        )
        previous = end
    return clamped


def assign_shots(
    spans: list[dict[str, float | str]],
    shots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """每句对上一个镜头。

    优先用镜头自己的 narration / subtitle 去对句子；对不上时按顺序分配。
    镜头比句子少时，多出来的句子仍挂在最后一个镜头上，时间不丢。
    """
    remaining = list(spans)
    cues: list[dict[str, Any]] = []
    if not shots:
        for index, span in enumerate(remaining, start=1):
            cues.append(_cue(f"shot_{index}", span))
        return cues

    used: set[int] = set()
    for shot_index, shot in enumerate(shots):
        shot_id = str(shot.get("id") or shot.get("shot_id") or f"shot_{shot_index + 1}")
        needle = str(shot.get("narration") or shot.get("subtitle") or "").strip()
        matched = _take_matching(remaining, needle) if needle else None
        if matched is None and remaining:
            matched = remaining.pop(0)
            used.add(shot_index)
        elif matched is not None:
            used.add(shot_index)
        if matched is not None:
            cues.append(_cue(shot_id, matched))

    if remaining:
        tail_id = str(shots[-1].get("id") or shots[-1].get("shot_id") or f"shot_{len(shots)}")
        cues.extend(_cue(tail_id, span) for span in remaining)
    return cues


def _take_matching(
    remaining: list[dict[str, float | str]],
    needle: str,
) -> dict[str, float | str] | None:
    """从还没分配的句子里取出和镜头旁白重合最高的一句。"""
    best_at = -1
    best_score = 0
    needle_chars = set(re.sub(r"[\s\W_]+", "", needle, flags=re.UNICODE))
    if not needle_chars:
        return None
    for index, span in enumerate(remaining):
        span_chars = set(re.sub(r"[\s\W_]+", "", str(span["text"]), flags=re.UNICODE))
        score = len(needle_chars & span_chars)
        if score > best_score:
            best_score = score
            best_at = index
    # 至少对上两个字，避免短句误配。
    if best_at < 0 or best_score < 2:
        return None
    return remaining.pop(best_at)


def _cue(shot_id: str, span: dict[str, float | str]) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "start": float(span["start"]),
        "end": float(span["end"]),
        "text": str(span["text"]),
    }


def _collect_words(chunks: Any) -> list[dict[str, Any]]:
    """从 edge-tts 的 WordBoundary 收词。offset 单位是 100 纳秒。"""
    words: list[dict[str, Any]] = []
    for chunk in chunks:
        if chunk.get("type") != "WordBoundary":
            continue
        text = str(chunk.get("text") or "")
        if not text.strip():
            continue
        offset = float(chunk.get("offset") or 0)
        length = float(chunk.get("duration") or 0)
        words.append(
            {
                "text": text,
                "start": offset / _TICKS_PER_SECOND,
                "end": (offset + length) / _TICKS_PER_SECOND,
            }
        )
    return words


def synthesize_edge(
    text: str,
    output_path: Path,
    voice: str,
    rate: float = 1.0,
) -> dict[str, Any]:
    """用 edge-tts 库合成，并拿到词级时间戳。库不可用时退回 CLI。"""
    if not text.strip():
        raise ValueError("speech text cannot be empty")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    voice_name = voice_name_for(voice)
    rate_arg = rate_arg_for(rate)
    words: list[dict[str, Any]] = []
    library_error = ""
    try:
        import edge_tts
    except ImportError as exc:
        library_error = str(exc)
        edge_tts = None  # type: ignore[assignment]
    if edge_tts is not None:
        communicate = edge_tts.Communicate(
            text,
            voice_name,
            rate=rate_arg,
            boundary="WordBoundary",
        )
        # 同一次流里写音频、收词。不能再调一次 stream，否则时间对不上这一份音频。
        with output_path.open("wb") as audio_file:
            for chunk in communicate.stream_sync():
                if chunk.get("type") == "audio":
                    audio_file.write(chunk["data"])
                elif chunk.get("type") == "WordBoundary":
                    words.append(chunk)
        words = _collect_words(words)
    else:
        _synthesize_edge_cli(text, output_path, voice_name, rate_arg)
    if not output_path.exists() or output_path.stat().st_size < 1000:
        detail = library_error or "edge-tts synthesis failed"
        raise RuntimeError(detail)
    return {
        "path": str(output_path),
        "provider": "edge-tts",
        "model": voice_name,
        "words": words,
        "word_timestamps": bool(words),
    }


def _synthesize_edge_cli(text: str, output_path: Path, voice_name: str, rate_arg: str) -> None:
    """库缺失时的退路。CLI 不回词级时间戳，调用方会走估算。"""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "edge_tts",
            "--voice",
            voice_name,
            "--rate",
            rate_arg,
            "--text",
            text,
            "--write-media",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "edge-tts synthesis failed")


def generate_edge_tts(
    text: str,
    output_path: Path,
    voice: str,
    rate: float = 1.0,
    shots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """合成配音并带回 shot 级 cues。编排只认返回值里的 cues。"""
    produced = synthesize_edge(text, Path(output_path), voice, rate)
    duration = probe_duration(Path(produced["path"]))
    sentences = split_sentences(text)
    spans, timing = align_sentences(sentences, list(produced.get("words") or []), duration)
    cues = assign_shots(spans, list(shots or []))
    event = {
        "type": "tts",
        "provider": "edge-tts",
        "model": produced["model"],
        "ok": True,
        "timing": timing,
        "word_count": len(produced.get("words") or []),
    }
    if timing == "estimated":
        event["estimated"] = True
        event["estimate_reason"] = "拿不到可用的词级时间戳，已按标点比例估算"
    return {
        "path": produced["path"],
        "provider": "edge-tts",
        "model": produced["model"],
        "duration": round(duration, 3),
        "cues": cues,
        "event": event,
    }


def build_audio(
    narration: str,
    voice: str,
    rate: float,
    music_mood: str,
    shots: list[dict[str, Any]],
    output_dir: Path,
    music_dir: Path | None = None,
) -> Any:
    """配音加选曲，返回通过 validate() 的 AudioResult。

    输入：旁白、声线、语速、音乐情绪、镜头列表。
    云 TTS 密钥没配时走 edge-tts，不假装已经接通付费接口。
    """
    from .contract import AudioResult, Cue
    from .music import select_music

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    voice_path = output_dir / "voice.mp3"
    spoken = synthesize_spoken(narration, voice_path, voice, rate, shots)
    if music_dir is None:
        music_dir = Path(__file__).resolve().parents[3] / "runtime" / "music"
    music = select_music(Path(music_dir), music_mood)
    # 文案句数多于镜头时，多出来的句子会挂在同一镜头上，时间可能重叠。
    # 契约不允许重叠，所以同一镜头收成一段：从最早的开始到最晚的结束。
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in spoken["cues"]:
        shot_id = str(item["shot_id"])
        start = float(item["start"])
        end = float(item["end"])
        text = str(item["text"])
        current = grouped.get(shot_id)
        if current is None:
            grouped[shot_id] = {"shot_id": shot_id, "start": start, "end": end, "text": text}
            order.append(shot_id)
            continue
        current["start"] = min(float(current["start"]), start)
        current["end"] = max(float(current["end"]), end)
        if text and text not in str(current["text"]):
            current["text"] = f"{current['text']}{text}"
    cues = [
        Cue(
            shot_id=shot_id,
            start=float(grouped[shot_id]["start"]),
            end=float(grouped[shot_id]["end"]),
            text=str(grouped[shot_id]["text"]),
        )
        for shot_id in order
    ]
    duration = float(spoken["duration"])
    if cues and cues[-1].end > duration:
        duration = cues[-1].end
    event = {
        "type": "audio",
        "provider": spoken["provider"],
        "model": spoken["model"],
        "ok": True,
        "timing": spoken["event"].get("timing"),
        "tts": spoken["event"],
        "music": {
            "path": music["path"],
            "mood": music["mood"],
            "category": music["category"],
            "provider": music["provider"],
        },
        # 渲染线消费。声音线不改 renderer.py 的 amix。
        "mix": music["duck"],
    }
    if spoken["event"].get("estimated"):
        event["estimated"] = True
        event["estimate_reason"] = spoken["event"].get("estimate_reason", "")
    result = AudioResult(
        voice_path=str(spoken["path"]),
        music_path=str(music["path"]),
        cues=cues,
        total_duration=round(duration, 3),
        cost=0.0,
        event=event,
    )
    result.validate()
    return result


def synthesize_spoken(
    narration: str,
    output_path: Path,
    voice: str,
    rate: float,
    shots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """先 edge-tts。失败再试 Windows 语音，并标明时间戳是估算。"""
    try:
        return generate_edge_tts(narration, output_path, voice, rate, shots)
    except (RuntimeError, OSError, ValueError) as exc:
        edge_error = str(exc)
    if not sys.platform.startswith("win"):
        raise RuntimeError(edge_error)
    wav_path = Path(output_path).with_suffix(".wav")
    generate_system_speech(narration, wav_path)
    duration = probe_duration(wav_path)
    sentences = split_sentences(narration)
    spans = estimate_spans(sentences, duration)
    cues = assign_shots(spans, list(shots or []))
    return {
        "path": str(wav_path),
        "provider": "windows-system-speech",
        "model": "installed-system-voice",
        "duration": round(duration, 3),
        "cues": cues,
        "event": {
            "type": "tts",
            "provider": "windows-system-speech",
            "ok": True,
            "timing": "estimated",
            "estimated": True,
            "estimate_reason": f"edge-tts 失败（{edge_error}），Windows 语音没有词级时间戳，已按标点比例估算",
        },
    }


def generate_system_speech(text: str, output_path: Path) -> dict[str, Any]:
    """Use an installed Windows speech voice without requiring another API key."""
    if not text.strip():
        raise ValueError("speech text cannot be empty")
    script = r"""
$ErrorActionPreference = "Stop"
$textPath = $args[0]
$outputPath = $args[1]
$text = [System.IO.File]::ReadAllText($textPath, [System.Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $synth.GetInstalledVoices()
$zh = $voices | Where-Object { $_.VoiceInfo.Culture.Name -like "zh-CN*" } | Select-Object -First 1
if ($null -ne $zh) {
  $synth.SelectVoice($zh.VoiceInfo.Name)
}
$synth.SetOutputToWaveFile($outputPath)
$synth.Speak($text)
$synth.Dispose()
"""
    text_path = output_path.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, str(text_path), str(output_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=90,
        )
    finally:
        text_path.unlink(missing_ok=True)
    if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "Windows system speech synthesis failed")
    duration = probe_duration(output_path)
    sentences = split_sentences(text)
    spans = estimate_spans(sentences, duration) if duration > 0 and sentences else []
    return {
        "path": str(output_path),
        "provider": "windows-system-speech",
        "model": "installed-system-voice",
        "duration": round(duration, 3),
        "cues": [_cue(f"shot_{index}", span) for index, span in enumerate(spans, start=1)],
        "event": {
            "type": "tts",
            "provider": "windows-system-speech",
            "ok": True,
            "timing": "estimated",
            "estimated": True,
            "estimate_reason": "Windows 语音没有词级时间戳，已按标点比例估算",
        },
    }


def main() -> int:
    """独立核对：python -m backend.app.providers.tts --text ... --out ... --dump-cues"""
    import argparse

    parser = argparse.ArgumentParser(description="合成带时间戳的配音")
    parser.add_argument("--text", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--voice", default="女声")
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--dump-cues", action="store_true")
    args = parser.parse_args()
    produced = generate_edge_tts(args.text, Path(args.out), args.voice, args.rate)
    print(f"[ok] {produced['path']} {produced['duration']}s timing={produced['event'].get('timing')}")
    if args.dump_cues:
        print(json.dumps(produced["cues"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
