from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio_runtime import AudioRuntimeClient


# edge-tts 的中文音色。这张表以实测 edge_tts.list_voices() 的 zh-CN 全集为准，
# 不要凭印象加音色名——写错会变成运行时的 404。
# 表里没有的字符串按 edge 的 ShortName 原样透传，所以直接填 zh-CN-* 也能跑。
EDGE_VOICES: dict[str, str] = {
    # 旧值保留为别名，历史任务的 voice 字段还要能跑
    "女声": "zh-CN-XiaoxiaoNeural",
    "男声": "zh-CN-YunxiNeural",
    "晓晓·温暖女声": "zh-CN-XiaoxiaoNeural",
    "晓伊·活泼女声": "zh-CN-XiaoyiNeural",
    "小北·东北女声": "zh-CN-liaoning-XiaobeiNeural",
    "小妮·陕西女声": "zh-CN-shaanxi-XiaoniNeural",
    "云希·清朗男声": "zh-CN-YunxiNeural",
    "云健·沉稳男声": "zh-CN-YunjianNeural",
    "云扬·播报男声": "zh-CN-YunyangNeural",
    "云夏·少年男声": "zh-CN-YunxiaNeural",
}

# 给前端下拉用的顺序：两位默认音色在前，其余按性别分组。
EDGE_VOICE_CHOICES: tuple[str, ...] = (
    "晓晓·温暖女声",
    "晓伊·活泼女声",
    "小北·东北女声",
    "小妮·陕西女声",
    "云希·清朗男声",
    "云健·沉稳男声",
    "云扬·播报男声",
    "云夏·少年男声",
)


def edge_voice_name(voice: str) -> str:
    """把用户选的音色名翻成 edge 的 ShortName。未知值原样返回。"""
    return EDGE_VOICES.get(str(voice or "").strip(), str(voice or "").strip())


# 句读停顿。edge-tts 免费接口不吃 SSML，插停顿只能靠"分句合成 + 句间补静音"。
# 逗号短、句号长。数值是按中文播报习惯取的，改之前先听成片。
CLAUSE_PAUSE_SEC: dict[str, float] = {
    "。": 0.34, "！": 0.34, "？": 0.34, "…": 0.34,
    ".": 0.34, "!": 0.34, "?": 0.34,
    "，": 0.20, "、": 0.20, "；": 0.20,
    ",": 0.20, ";": 0.20,
}
DEFAULT_CLAUSE_PAUSE_SEC = 0.24

_CLAUSE_ENDINGS = "。！？…，、；.!?,;"


def split_clauses(text: str) -> list[str]:
    """按标点把一句话切成朗读片段，标点跟着前一段走。

    标点保留是有意的：edge-tts 靠句末标点决定降调，去掉标点会让每段听起来
    像被硬切断。返回值里不会出现纯标点或空白段。
    """
    clauses: list[str] = []
    current = ""
    for char in str(text or ""):
        current += char
        if char in _CLAUSE_ENDINGS:
            if current.strip(_CLAUSE_ENDINGS + " \t　"):
                clauses.append(current.strip())
            current = ""
    if current.strip():
        clauses.append(current.strip())
    return clauses


def clause_pause_sec(clause: str) -> float:
    """这一段读完后该停多久，看它的收尾标点。"""
    stripped = str(clause or "").rstrip()
    if stripped and stripped[-1] in CLAUSE_PAUSE_SEC:
        return CLAUSE_PAUSE_SEC[stripped[-1]]
    return DEFAULT_CLAUSE_PAUSE_SEC


@dataclass
class TTSResult:
    path: Path
    provider: str
    model: str
    event: dict[str, object]


class TTSProvider:
    name = "base"

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0) -> TTSResult:
        raise NotImplementedError


class OpenAICompatibleTTSProvider(TTSProvider):
    name = "openai-compatible-tts"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def synthesize(self, text: str, output_path: Path, voice: str, rate: float = 1.0) -> TTSResult:
        if not self.base_url or not self.api_key:
            raise RuntimeError("TTS provider 未配置 base_url 或 api_key")
        body = {"model": self.model, "input": text, "voice": voice, "response_format": "wav", "speed": rate}
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


def generate_edge_tts(
    text: str,
    output_path: Path,
    voice: str,
    rate: float = 1.0,
    runtime: AudioRuntimeClient | None = None,
    pitch_hz: float = 0.0,
) -> dict[str, Any]:
    """Use Edge TTS through the isolated Python 3.11 audio runtime."""
    if not text.strip():
        raise ValueError("speech text cannot be empty")
    voice_name = edge_voice_name(voice)
    if not voice_name:
        raise ValueError("edge TTS voice cannot be empty")
    rate_percent = round((float(rate) - 1.0) * 100)
    rate_arg = f"{rate_percent:+d}%"
    # edge 的音高按赫兹偏移给，0 表示不变调。神经音色做音高偏移容易发假，
    # 所以默认 0，只在用户显式调整时才动。
    pitch_arg = f"{round(float(pitch_hz)):+d}Hz"
    if runtime is None:
        raise RuntimeError(
            "Edge TTS requires the isolated audio runtime. "
            "Run scripts/setup-audio-runtime.ps1 first."
        )
    return runtime.synthesize_edge_tts(text, output_path, voice_name, rate_arg, pitch_arg)


def generate_system_speech(
    text: str,
    output_path: Path,
    voice: str = "女声",
    rate: float = 1.0,
) -> dict[str, Any]:
    """Use an installed Windows speech voice without requiring another API key."""
    if not text.strip():
        raise ValueError("speech text cannot be empty")
    script = r"""
$ErrorActionPreference = "Stop"
$textPath = $env:ZJ_TTS_TEXT_PATH
$outputPath = $env:ZJ_TTS_OUTPUT_PATH
$voicePreference = $env:ZJ_TTS_VOICE
$rateValue = [int]$env:ZJ_TTS_RATE
$text = [System.IO.File]::ReadAllText($textPath, [System.Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = $synth.GetInstalledVoices()
$zhVoices = @($voices | Where-Object { $_.VoiceInfo.Culture.Name -like "zh-CN*" })
$gender = if ($voicePreference -like "*男声*" -or $voicePreference -like "*Yun*") { "Male" } else { "Female" }
$zh = $zhVoices | Where-Object { $_.VoiceInfo.Gender.ToString() -eq $gender } | Select-Object -First 1
if ($null -eq $zh) { $zh = $zhVoices | Select-Object -First 1 }
if ($null -ne $zh) {
  $synth.SelectVoice($zh.VoiceInfo.Name)
}
$synth.Rate = [Math]::Max(-10, [Math]::Min(10, $rateValue))
$synth.SetOutputToWaveFile($outputPath)
$synth.Speak($text)
$selectedVoice = $synth.Voice.Name
$synth.Dispose()
$selectedVoice
"""
    text_path = output_path.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    environment = os.environ.copy()
    environment["ZJ_TTS_TEXT_PATH"] = str(text_path.resolve())
    environment["ZJ_TTS_OUTPUT_PATH"] = str(output_path.resolve())
    environment["ZJ_TTS_VOICE"] = voice
    environment["ZJ_TTS_RATE"] = str(round((float(rate) - 1.0) * 5))
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=environment,
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
    return {
        "path": str(output_path),
        "provider": "windows-system-speech",
        "model": (result.stdout or "installed-system-voice").strip() or "installed-system-voice",
    }
