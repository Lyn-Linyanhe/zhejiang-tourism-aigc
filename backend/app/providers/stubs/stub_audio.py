"""声音 stub —— P3 的替身，第一天就能让 P1 跑通全链路。

产出带真实时间戳的 cues，形状与 contract.AudioResult 完全一致。
用纯 Python 的 wave 模块生成正弦波，不需要 ffmpeg，也不需要任何 API。

用法（独立验证）：
    python stub_audio.py --out-dir /tmp/fake --shot-count 6 --duration 30
    python stub_audio.py --out-dir /tmp/fake --dump-cues

P1 接入方式：注册成一个 TTS provider；真实 TTS 全部失败时降级到它。
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AudioResult, Cue  # noqa: E402


SAMPLE_RATE = 44100

# 不同情绪用不同基频，模拟背景音乐的情绪差异
MOOD_FREQUENCIES = {
    "舒缓": 220,
    "热烈": 330,
    "雅致": 260,
    "轻快": 440,
}


def _write_tone(
    path: Path,
    duration: float,
    frequency: float,
    volume: float = 0.12,
    wobble: bool = False,
) -> None:
    """写一段正弦波 wav。纯 stdlib，不依赖 ffmpeg。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(SAMPLE_RATE * max(0.05, duration)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        data = bytearray()
        for index in range(frames):
            t = index / SAMPLE_RATE
            value = math.sin(2 * math.pi * frequency * t)
            if wobble:
                value *= 0.6 + 0.4 * math.sin(2 * math.pi * 0.4 * t)
            data += struct.pack("<h", int(max(-1.0, min(1.0, value * volume)) * 32767))
        handle.writeframes(bytes(data))


def _build_cues(shots: list[dict[str, Any]]) -> list[Cue]:
    """按镜头名义时长铺 cues。真实 TTS 会按音频实测时长回填。"""
    cues: list[Cue] = []
    elapsed = 0.0
    for index, shot in enumerate(shots, start=1):
        shot_id = str(shot.get("id") or f"shot_{index}")
        duration = float(shot.get("duration_sec", 5.0))
        cues.append(
            Cue(
                shot_id=shot_id,
                start=round(elapsed, 3),
                end=round(elapsed + duration, 3),
                text=str(shot.get("narration") or shot.get("subtitle") or f"测试旁白 {index}"),
            )
        )
        elapsed += duration
    return cues


class StubTTSProvider:
    """与 contract.AudioProviderProtocol 形状一致。"""

    name = "stub-tts"
    cost_per_call = 0.0

    def __init__(self, out_dir: Path, frequency: float = 420.0) -> None:
        self.out_dir = Path(out_dir)
        self.frequency = frequency

    def synthesize(
        self,
        text: str,
        output_path: Any,
        voice: str = "女声",
        rate: float = 1.0,
    ) -> dict[str, Any]:
        """生成一段与文本长度成正比的占位配音。

        真实 TTS 返回的是实测时长；这里按字数估算，形状保持一致。
        """
        target = Path(output_path)
        # 按中文播报速度估算：约 5 字/秒
        estimated = max(1.0, len(text.strip()) / 5.0) / max(0.5, rate)
        _write_tone(target, estimated, self.frequency, volume=0.10, wobble=True)
        return {
            "path": str(target),
            "provider": self.name,
            "model": "stub-sine-v1",
            "duration": round(estimated, 3),
            "event": {"type": "tts", "provider": self.name, "ok": True},
        }


class StubMusicProvider:
    name = "stub-music"
    cost_per_call = 0.0

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)

    def generate(self, mood: str, duration: float, output_path: Any) -> dict[str, Any]:
        target = Path(output_path)
        frequency = MOOD_FREQUENCIES.get(mood, 220)
        _write_tone(target, duration, frequency, volume=0.06, wobble=True)
        return {
            "path": str(target),
            "provider": self.name,
            "model": f"stub-sine-{frequency}hz",
            "mood": mood,
            "event": {"type": "music", "provider": self.name, "mood": mood, "ok": True},
        }


def build_audio_result(
    shots: list[dict[str, Any]],
    out_dir: Path,
    mood: str = "舒缓",
) -> AudioResult:
    """一步产出完整的 AudioResult，供 P1 的 stub 链路直接使用。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cues = _build_cues(shots)
    total = cues[-1].end if cues else 0.0

    tts = StubTTSProvider(out_dir)
    music = StubMusicProvider(out_dir)
    voice = tts.synthesize(
        "".join(cue.text for cue in cues), out_dir / "voice.wav", "女声", 1.0
    )
    track = music.generate(mood, total, out_dir / "music.wav")

    result = AudioResult(
        voice_path=voice["path"],
        music_path=track["path"],
        cues=cues,
        total_duration=round(total, 3),
        event={"type": "audio", "provider": "stub", "ok": True},
    )
    result.validate()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 stub 音频与 cues")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--shot-count", type=int, default=6)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--mood", default="舒缓")
    parser.add_argument("--dump-cues", action="store_true")
    args = parser.parse_args()

    unit = round(args.duration / args.shot_count, 3)
    shots = [
        {
            "id": f"shot_{index}",
            "order": index,
            "duration_sec": unit,
            "narration": f"这是第 {index} 个镜头的测试旁白。",
        }
        for index in range(1, args.shot_count + 1)
    ]
    result = build_audio_result(shots, Path(args.out_dir), args.mood)
    print(f"[ok] 配音 {result.voice_path}")
    print(f"[ok] 音乐 {result.music_path}")
    print(f"[ok] 总时长 {result.total_duration}s，{len(result.cues)} 条 cues")
    if args.dump_cues:
        print(json.dumps([c.__dict__ for c in result.cues], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
