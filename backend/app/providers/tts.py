from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audio_runtime import AudioRuntimeClient


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


def generate_edge_tts(
    text: str,
    output_path: Path,
    voice: str,
    rate: float = 1.0,
    runtime: AudioRuntimeClient | None = None,
) -> dict[str, Any]:
    """Use Edge TTS through the isolated Python 3.11 audio runtime."""
    if not text.strip():
        raise ValueError("speech text cannot be empty")
    voice_name = {
        "女声": "zh-CN-XiaoxiaoNeural",
        "男声": "zh-CN-YunxiNeural",
    }.get(voice, voice)
    if not voice_name:
        raise ValueError("edge TTS voice cannot be empty")
    rate_percent = round((float(rate) - 1.0) * 100)
    rate_arg = f"{rate_percent:+d}%"
    if runtime is None:
        raise RuntimeError(
            "Edge TTS requires the isolated audio runtime. "
            "Run scripts/setup-audio-runtime.ps1 first."
        )
    return runtime.synthesize_edge_tts(text, output_path, voice_name, rate_arg)


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
$gender = if ($voicePreference -eq "男声") { "Male" } else { "Female" }
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
