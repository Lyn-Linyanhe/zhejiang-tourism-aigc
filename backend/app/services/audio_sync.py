"""Measured per-shot narration assembly and cue generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..providers.audio_runtime import AudioRuntimeClient
from ..providers.contract import AudioResult, Cue
from ..providers.media_ffmpeg import run_ffmpeg, validate_audio_file
from ..providers.tts import (
    OpenAICompatibleTTSProvider,
    generate_edge_tts,
    generate_system_speech,
    edge_voice_name,
)
from .voice_studio import plan_segments, phrase_parameters
from ..providers.tianyi import runtime as tianyi_runtime, MODEL_ID as TIANYI_MODEL_ID


SILENCE_BETWEEN_SHOTS = 0.150

# 为了把旁白压进用户选的秒数，最多把语速提这么多倍。再快就不像人说话了，
# 宁可留一点裁切，也不要交付一个念得像按了快进的配音。
MAX_RATE_SCALE = 1.25


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
        self._cache_lock = threading.RLock()

    @staticmethod
    def _cache_key(
        provider: str, model: str, voice: str, rate: float, text: str, pitch: float = 0.0
    ) -> str:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = json.dumps(
            {
                "provider": provider,
                "version": "voice-studio-v1",
                "model": model,
                "voice": voice,
                "rate": round(float(rate), 4),
                # 音高必须进缓存键：只改音高时若键不变，会命中旧音频，
                # 用户看到"改了没效果"。
                "pitch": round(float(pitch), 2),
                "text_sha256": text_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _synthesize_cached(
        self, text: str, voice: str, rate: float, force_provider: str = "", pitch: float = 0.0,
    ) -> tuple[Path, dict[str, Any]]:
        # Previews and rendering share files; avoid simultaneous partial writes.
        with self._cache_lock:
            return self._synthesize_cached_locked(text, voice, rate, force_provider, pitch)

    def _synthesize_cached_locked(
        self,
        text: str,
        voice: str,
        rate: float,
        force_provider: str = "",
        pitch: float = 0.0,
    ) -> tuple[Path, dict[str, Any]]:
        attempts: list[tuple[str, str]] = []
        preference = force_provider or str(getattr(self.settings, "tts_provider", "windows"))
        if preference == "tianyi":
            attempts.append(("tianyi-local", TIANYI_MODEL_ID))
        elif preference in {"windows", "windows-system-speech"}:
            attempts.append(("windows-system-speech", "installed-system-voice"))
        elif preference in {"openai", "openai-compatible-tts"}:
            if not self.settings.tts_base_url or not self.settings.tts_api_key:
                raise RuntimeError("TTS_PROVIDER=openai requires TTS_BASE_URL and TTS_API_KEY")
            attempts.append(("openai-compatible-tts", self.settings.tts_model))
        elif preference == "edge":
            edge_voice = edge_voice_name(voice)
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
                attempts.append(("edge-tts", edge_voice_name(voice)))
                attempts.append(("windows-system-speech", "installed-system-voice"))
        else:
            raise ValueError(f"Unsupported TTS_PROVIDER: {preference}")

        errors: list[str] = []
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for provider, model in attempts:
            key = self._cache_key(provider, model, voice, rate, text, pitch)
            cached = self.cache_dir / f"{key}.wav"
            metadata = cached.with_suffix(".json")
            if cached.is_file():
                try:
                    info = probe_audio(cached)
                    if info["sample_rate"] == 48000 and info["channels"] == 1:
                        saved = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
                        return cached, {
                            "provider": provider,
                            "model": model,
                            "cache_key": key,
                            "cache_hit": True,
                            **saved,
                            "fallback_errors": errors.copy(),
                        }
                except Exception:
                    cached.unlink(missing_ok=True)

            raw_suffix = ".mp3" if provider == "edge-tts" else ".wav"
            raw = self.cache_dir / f"{key}.raw{raw_suffix}"
            try:
                if provider == "tianyi-local":
                    local = tianyi_runtime.synthesize(text, raw, rate)
                    model = str(local["model"])
                elif provider == "openai-compatible-tts":
                    result = OpenAICompatibleTTSProvider(
                        self.settings.tts_base_url,
                        self.settings.tts_api_key,
                        self.settings.tts_model,
                    ).synthesize(text, raw, "alloy" if (voice == "女声" or "女声" in voice) else "onyx", rate=rate)
                    model = result.model
                elif provider == "edge-tts":
                    edge = generate_edge_tts(
                        text, raw, voice, rate, self.runtime, pitch_hz=pitch
                    )
                    model = str(edge.get("model", model))
                else:
                    windows = generate_system_speech(text, raw, voice, rate)
                    model = str(windows.get("model", model))
                info = _atomic_normalize(raw, cached)
                raw.unlink(missing_ok=True)
                details = {"model": model, "pitch_applied": provider == "edge-tts"}
                if provider == "tianyi-local":
                    details.update({"device":local.get("device"), "source":"community", "offline":True})
                metadata.write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
                return cached, {
                    "provider": provider,
                    "model": model,
                    "cache_key": key,
                    "cache_hit": False,
                    "duration_sec": info["duration"],
                    **details,
                    "fallback_errors": errors.copy(),
                }
            except Exception as exc:
                raw.unlink(missing_ok=True)
                errors.append(f"{provider}: {exc}")
                if force_provider or provider == "openai-compatible-tts":
                    break
        raise RuntimeError("No usable per-shot TTS provider: " + " | ".join(errors))

    @staticmethod
    def _plan_segments(text: str, subtitle: str) -> list[tuple[str, str]]:
        """按完整句子配对字幕，逗号/顿号保留在同一次合成中。"""
        return plan_segments(text, subtitle)

    def _silence_file(self, output_dir: Path, seconds: float) -> Path:
        """按时长生成（并复用）一段静音。文件名带毫秒数，便于分辨句间/镜间停顿。"""
        key = int(round(seconds * 1000))
        target = output_dir / f"silence-{key}ms.wav"
        if target.is_file():
            return target
        temporary = output_dir / f"silence-{key}ms.tmp.wav"
        generated = run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                f"{seconds:.3f}",
                "-c:a",
                "pcm_s16le",
                str(temporary),
            ]
        )
        if generated.returncode != 0:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(generated.stderr.strip() or "Cannot create silence segment")
        os.replace(temporary, target)
        return target

    def _assemble_voice(
        self,
        entries: list[tuple[Path, float]],
        output_dir: Path,
        expected_duration: float,
    ) -> Path:
        """按 (片段, 片段之后的停顿秒数) 顺序拼接成人声轨。"""
        concat_entries: list[Path] = []
        for index, (segment, gap) in enumerate(entries):
            concat_entries.append(segment)
            if gap > 0 and index < len(entries) - 1:
                concat_entries.append(self._silence_file(output_dir, gap))
        concat = output_dir / "voice-concat.txt"
        concat.write_text(
            "\n".join(
                f"file '{path.relative_to(output_dir).as_posix().replace(chr(39), chr(39) + chr(92) + chr(39))}'"
                for path in concat_entries
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

    def _synthesize_timeline(
        self,
        shots: list[Any],
        voice: str,
        rate: float,
        pitch: float,
        force_provider: str,
        segment_dir: Path,
        style: str = "natural",
        expressiveness: float = 0.5,
    ) -> dict[str, Any]:
        """按镜头逐句合成，返回整条人声时间轴。

        单独成方法是因为超时长时要换一档语速重跑：合成之前量不出真实总长，
        必须先跑一轮才知道该不该提速。
        """
        cues: list[Cue] = []
        traces: list[dict[str, Any]] = []
        entries: list[tuple[Path, float]] = []
        elapsed = 0.0
        speech_total = 0.0
        synthesized = 0
        for index, shot in enumerate(shots):
            shot_id = str(getattr(shot, "id", f"shot_{index + 1}"))
            text = str(getattr(shot, "narration", "") or "").strip()
            subtitle = str(getattr(shot, "subtitle", "") or text).strip()
            if not text:
                raise ValueError(f"Shot {shot_id} narration is empty")
            uploaded_value = str(getattr(shot, "audio_local_path", "") or "").strip()
            uploaded = Path(uploaded_value) if uploaded_value else None
            # 完整句子由引擎连读，避免逗号处重启韵律；上传音频不切分。
            use_upload = bool(uploaded and uploaded.is_file())
            if force_provider in {"upload", "xstudio"} and not use_upload:
                raise ValueError(f"镜头 {shot_id} 尚未绑定配音音频；请在上传配音区域选择该镜头并上传音频。")
            plan = [(text, subtitle)] if use_upload else self._plan_segments(text, subtitle)
            shot_start = elapsed
            for piece_index, (speech, cue_text) in enumerate(plan):
                is_last_piece = piece_index == len(plan) - 1
                params = phrase_parameters(speech, style=style, strength=expressiveness, rate=rate, pitch=pitch, clip=shot)
                if use_upload:
                    source = uploaded
                    trace = {
                        "provider": "local-upload",
                        "model": "uploaded-shot-audio",
                        "cache_hit": False,
                    }
                else:
                    source, trace = self._synthesize_cached(
                        speech, voice, params["rate"], force_provider, params["pitch_hz"]
                    )
                    synthesized += 1
                trace["source_path"] = str(source.resolve())
                normalized = segment_dir / (
                    f"{shot_id}.wav" if len(plan) == 1 else f"{shot_id}-{piece_index + 1}.wav"
                )
                info = _atomic_normalize(source, normalized)
                duration = float(info["duration"])
                cue = Cue(
                    shot_id=shot_id,
                    start=round(elapsed, 6),
                    end=round(elapsed + duration, 6),
                    text=cue_text,
                )
                cue.validate()
                cues.append(cue)
                trace.update(
                    {
                        "shot_id": shot_id,
                        "piece": piece_index + 1,
                        "segment_path": str(normalized),
                        "segment_duration": duration,
                        "sample_rate": info["sample_rate"],
                        "channels": info["channels"],
                        "text": speech,
                        "start": cue.start,
                        "end": cue.end,
                        "prosody": params if not use_upload else {"source": "imported", "applied": False},
                    }
                )
                traces.append(trace)
                if is_last_piece:
                    requested_gap = getattr(shot, "voice_pause_after", None)
                    gap = (SILENCE_BETWEEN_SHOTS if requested_gap is None else requested_gap) if index < len(shots) - 1 else 0.0
                else:
                    # The engine already renders sentence punctuation. Do not
                    # insert a second fixed pause at every comma/full stop.
                    gap = 0.0
                entries.append((normalized, gap))
                speech_total += duration
                elapsed = cue.end + gap
            # 镜头时长必须**含**镜后的停顿：各镜时长之和要等于人声轨总长，
            # 画面时间轴才和人声时间轴严丝合缝。少算这一段，每过一个镜头边界
            # 画面就会比声音超前 150ms，八镜累计超过一秒。
            shot.duration_sec = round(elapsed - shot_start, 3)
        return {
            "cues": cues,
            "traces": traces,
            "entries": entries,
            "elapsed": elapsed,
            # 语速只作用在说话的那部分，镜间静音不会跟着缩短，
            # 所以提速比例要按"语音时长"算，不能按总时长算。
            "speech_total": speech_total,
            "synthesized_count": synthesized,
        }

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
        pitch: float = 0.0,
        style: str = "natural",
        expressiveness: float = 0.5,
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

        timeline = self._synthesize_timeline(
            shots, voice, rate, pitch, force_provider, segment_dir, style, expressiveness
        )
        rate_scale = 1.0
        # 中文 TTS 实测在 3.4~3.9 字/秒之间浮动，prompt 里的字数预算压不住这 12% 的
        # 波动。实测总长一旦超出用户选的秒数，超出的部分会被渲染器裁掉——最后一句
        # 听着就是断的。语速是手边唯一能压时长的旋钮，按比值提一档再合成一次。
        # 上传的整段音频提不了速（那是用户给的原始录音），这种情况不重跑。
        silence_total = timeline["elapsed"] - timeline["speech_total"]
        speech_budget = float(target_duration) - silence_total
        if (
            timeline["synthesized_count"] > 0
            and target_duration > 0
            and speech_budget > 0.1
            and timeline["speech_total"] > speech_budget + 0.05
        ):
            scale = min(MAX_RATE_SCALE, timeline["speech_total"] / speech_budget)
            if scale > 1.001:
                rate_scale = scale
                timeline = self._synthesize_timeline(
                    shots, voice, rate * scale, pitch, force_provider, segment_dir, style, expressiveness
                )

        cues: list[Cue] = timeline["cues"]
        traces: list[dict[str, Any]] = timeline["traces"]
        entries: list[tuple[Path, float]] = timeline["entries"]
        elapsed: float = timeline["elapsed"]

        voice_path = self._assemble_voice(entries, output_dir, elapsed)
        measured_total = probe_audio(voice_path)["duration"]
        if abs(measured_total - elapsed) > 0.05:
            raise RuntimeError(
                f"Assembled voice duration differs from measured cues by {abs(measured_total - elapsed):.3f}s"
            )
        if shots:
            shots[-1].duration_sec += measured_total - elapsed
        # 编排层从 event 里读 voice_provider / voice_model，缺了会显示成 "none"，
        # 让已经合成好的配音看起来像没生成。这里按段汇总实际用到的 provider。
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
                "segment_count": len(entries),
                "clause_level_cues": False,
                "sentence_level_cues": True,
                "prosody_style": style,
                "expressiveness": expressiveness,
                # >1 表示因为超出用户选的秒数而提过语速，值就是实际倍数。
                # 留档是为了以后回看"这段配音为什么比平时快"。
                "rate_scale": round(rate_scale, 3),
                "voice_provider": ",".join(
                    dict.fromkeys(
                        str(item["provider"]) for item in traces if item.get("provider")
                    )
                )
                or "none",
                "voice_model": ",".join(
                    dict.fromkeys(
                        str(item["model"]) for item in traces if item.get("model")
                    )
                )
                or "none",
            },
        )
        result.validate()
        return result
