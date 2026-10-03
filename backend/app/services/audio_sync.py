"""Measured per-shot narration assembly and cue generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..providers.audio_runtime import AudioRuntimeClient
from ..providers.contract import AudioResult, Cue
from ..providers.media_ffmpeg import run_ffmpeg, validate_audio_file
from ..providers.tts import OpenAICompatibleTTSProvider, generate_edge_tts, generate_system_speech


SILENCE_BETWEEN_SHOTS = 0.150


def probe_audio(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate,channels,codec_name:format=duration",
            "-of",
            "json",
            path.name,
        ],
        cwd=path.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"ffprobe failed: {path}")
    payload = json.loads(completed.stdout)
    stream = (payload.get("streams") or [{}])[0]
    return {
        "duration": float(payload.get("format", {}).get("duration", 0.0)),
        "sample_rate": int(stream.get("sample_rate", 0) or 0),
        "channels": int(stream.get("channels", 0) or 0),
        "codec_name": str(stream.get("codec_name", "")),
    }


def _atomic_normalize(source: Path, output: Path) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".tmp" + output.suffix)
    result = run_ffmpeg(
        [
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-sample_fmt",
            "s16",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ]
    )
    if result.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or "Audio normalization failed")
    validate_audio_file(temporary)
    info = probe_audio(temporary)
    if info["sample_rate"] != 48000 or info["channels"] != 1:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Normalized shot audio is not 48 kHz mono")
    os.replace(temporary, output)
    return info


class PerShotAudioBuilder:
    def __init__(self, settings: Any, runtime: AudioRuntimeClient) -> None:
        self.settings = settings
        self.runtime = runtime
        self.cache_dir = settings.data_dir / "audio-cache"

    @staticmethod
    def _cache_key(provider: str, model: str, voice: str, rate: float, text: str) -> str:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = json.dumps(
            {
                "provider": provider,
                "model": model,
                "voice": voice,
                "rate": round(float(rate), 4),
                "text_sha256": text_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _synthesize_cached(
        self,
        text: str,
        voice: str,
        rate: float,
        force_provider: str = "",
    ) -> tuple[Path, dict[str, Any]]:
        attempts: list[tuple[str, str]] = []
        preference = force_provider or str(getattr(self.settings, "tts_provider", "windows"))
        if preference in {"windows", "windows-system-speech"}:
            attempts.append(("windows-system-speech", "installed-system-voice"))
        elif preference in {"openai", "openai-compatible-tts"}:
            if not self.settings.tts_base_url or not self.settings.tts_api_key:
                raise RuntimeError("TTS_PROVIDER=openai requires TTS_BASE_URL and TTS_API_KEY")
            attempts.append(("openai-compatible-tts", self.settings.tts_model))
        elif preference == "edge":
            edge_voice = "zh-CN-XiaoxiaoNeural" if voice == "女声" else "zh-CN-YunxiNeural"
            attempts.extend(
                [
                    ("edge-tts", edge_voice),
                    ("windows-system-speech", "installed-system-voice"),
                ]
            )
        elif preference == "auto":
            if self.settings.tts_base_url and self.settings.tts_api_key:
                attempts.append(("openai-compatible-tts", self.settings.tts_model))
            else:
                attempts.append(("windows-system-speech", "installed-system-voice"))
        else:
            raise ValueError(f"Unsupported TTS_PROVIDER: {preference}")

        errors: list[str] = []
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for provider, model in attempts:
            key = self._cache_key(provider, model, voice, rate, text)
            cached = self.cache_dir / f"{key}.wav"
            if cached.is_file():
                try:
                    info = probe_audio(cached)
                    if info["sample_rate"] == 48000 and info["channels"] == 1:
                        return cached, {
                            "provider": provider,
                            "model": model,
                            "cache_key": key,
                            "cache_hit": True,
                        }
                except Exception:
                    cached.unlink(missing_ok=True)

            raw_suffix = ".mp3" if provider == "edge-tts" else ".wav"
            raw = self.cache_dir / f"{key}.raw{raw_suffix}"
            try:
                if provider == "openai-compatible-tts":
                    result = OpenAICompatibleTTSProvider(
                        self.settings.tts_base_url,
                        self.settings.tts_api_key,
                        self.settings.tts_model,
                    ).synthesize(text, raw, "alloy" if voice == "女声" else "onyx")
                    model = result.model
                elif provider == "edge-tts":
                    edge = generate_edge_tts(text, raw, voice, rate, self.runtime)
                    model = str(edge.get("model", model))
                else:
                    windows = generate_system_speech(text, raw, voice, rate)
                    model = str(windows.get("model", model))
                info = _atomic_normalize(raw, cached)
                raw.unlink(missing_ok=True)
                return cached, {
                    "provider": provider,
                    "model": model,
                    "cache_key": key,
                    "cache_hit": False,
                    "duration_sec": info["duration"],
                }
            except Exception as exc:
                raw.unlink(missing_ok=True)
                errors.append(f"{provider}: {exc}")
                if force_provider or provider == "openai-compatible-tts":
                    break
        raise RuntimeError("No usable per-shot TTS provider: " + " | ".join(errors))

    def _assemble_voice(
        self,
        segment_paths: list[Path],
        output_dir: Path,
        expected_duration: float,
    ) -> Path:
        silence = output_dir / "silence-150ms.wav"
        temporary_silence = output_dir / "silence-150ms.tmp.wav"
        generated = run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                f"{SILENCE_BETWEEN_SHOTS:.3f}",
                "-c:a",
                "pcm_s16le",
                str(temporary_silence),
            ]
        )
        if generated.returncode != 0:
            raise RuntimeError(generated.stderr.strip() or "Cannot create inter-shot silence")
        os.replace(temporary_silence, silence)

        entries: list[Path] = []
        for index, segment in enumerate(segment_paths):
            entries.append(segment)
            if index < len(segment_paths) - 1:
                entries.append(silence)
        concat = output_dir / "voice-concat.txt"
        concat.write_text(
            "\n".join(
                f"file '{path.relative_to(output_dir).as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'"
                for path in entries
            ),
            encoding="utf-8",
        )
        output = output_dir / "voice.wav"
        temporary = output_dir / "voice.tmp.wav"
        result = run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat.name,
                "-t",
                f"{expected_duration:.6f}",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                temporary.name,
            ],
            cwd=output_dir,
        )
        if result.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(result.stderr.strip() or "Per-shot voice concat failed")
        validate_audio_file(temporary)
        os.replace(temporary, output)
        return output

    def build(
        self,
        task_id: str,
        shots: list[Any],
        *,
        voice: str,
        rate: float,
        target_duration: float,
        output_dir: Path | None = None,
        force_provider: str = "",
    ) -> AudioResult:
        output_dir = output_dir or self.settings.media_dir / task_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if voice == "无配音":
            return AudioResult(
                voice_path="",
                music_path="",
                cues=[],
                total_duration=float(target_duration),
                target_duration=float(target_duration),
                timing_source="nominal-no-voice",
                event={"type": "audio", "voice_provider": "none"},
            )

        segment_dir = output_dir / "voice-segments"
        segment_dir.mkdir(parents=True, exist_ok=True)
        cues: list[Cue] = []
        traces: list[dict[str, Any]] = []
        segment_paths: list[Path] = []
        elapsed = 0.0
        for index, shot in enumerate(shots):
            text = str(getattr(shot, "narration", "") or "").strip()
            subtitle = str(getattr(shot, "subtitle", "") or text).strip()
            if not text:
                raise ValueError(f"Shot {getattr(shot, 'id', index + 1)} narration is empty")
            uploaded_value = str(getattr(shot, "audio_local_path", "") or "").strip()
            uploaded = Path(uploaded_value) if uploaded_value else None
            if uploaded and uploaded.is_file():
                source = uploaded
                trace = {
                    "provider": "local-upload",
                    "model": "uploaded-shot-audio",
                    "cache_hit": False,
                }
            else:
                source, trace = self._synthesize_cached(text, voice, rate, force_provider)
            trace["source_path"] = str(source.resolve())
            normalized = segment_dir / f"{getattr(shot, 'id', f'shot_{index + 1}')}.wav"
            info = _atomic_normalize(source, normalized)
            duration = float(info["duration"])
            cue = Cue(
                shot_id=str(getattr(shot, "id", f"shot_{index + 1}")),
                start=round(elapsed, 6),
                end=round(elapsed + duration, 6),
                text=subtitle,
            )
            cue.validate()
            cues.append(cue)
            segment_paths.append(normalized)
            trace.update(
                {
                    "shot_id": cue.shot_id,
                    "segment_path": str(normalized),
                    "segment_duration": duration,
                    "sample_rate": info["sample_rate"],
                    "channels": info["channels"],
                }
            )
            traces.append(trace)
            shot.duration_sec = duration + (SILENCE_BETWEEN_SHOTS if index < len(shots) - 1 else 0.0)
            elapsed = cue.end + (SILENCE_BETWEEN_SHOTS if index < len(shots) - 1 else 0.0)

        voice_path = self._assemble_voice(segment_paths, output_dir, elapsed)
        measured_total = probe_audio(voice_path)["duration"]
        if abs(measured_total - elapsed) > 0.05:
            raise RuntimeError(
                f"Assembled voice duration differs from measured cues by {abs(measured_total - elapsed):.3f}s"
            )
        if shots:
            shots[-1].duration_sec += measured_total - elapsed
        result = AudioResult(
            voice_path=str(voice_path),
            music_path="",
            cues=cues,
            total_duration=measured_total,
            target_duration=float(target_duration),
            timing_source="measured-shot-audio",
            provider_trace=traces,
            event={
                "type": "audio",
                "silence_between_shots_ms": round(SILENCE_BETWEEN_SHOTS * 1000),
                "segment_count": len(segment_paths),
            },
        )
        result.validate()
        return result
