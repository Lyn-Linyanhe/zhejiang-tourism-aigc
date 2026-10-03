"""Python 3.11 worker for optional audio AI dependencies.

The main HTTP service intentionally stays on the standard-library Python 3.14
runtime. This module is launched by ``AudioRuntimeClient`` with the isolated
``.venv-audio`` interpreter and communicates through one UTF-8 JSON request on
stdin plus one marker-prefixed JSON response on stdout.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import socket
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


RESULT_MARKER = "AUDIO_RUNTIME_RESULT:"


def _use_system_certificate_store() -> None:
    """Use the Windows trust store for model downloads behind local proxies."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except (ImportError, RuntimeError):
        # Non-Windows hosts and minimal installs can keep Python's default SSL
        # behavior.  A download error is still serialized by the worker.
        pass


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def _probe_duration(path: Path) -> float:
    """Probe by basename from the parent directory for Windows Unicode safety."""
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path.name,
        ],
        cwd=path.parent,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"ffprobe failed for {path.name}")
    return float(completed.stdout.strip())


def _normalized_segments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        words: list[dict[str, Any]] = []
        for word in item.get("words") or []:
            if word.get("start") is None or word.get("end") is None:
                continue
            words.append(
                {
                    "text": str(word.get("word") or word.get("text") or "").strip(),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                    "score": float(word.get("score", 0.0)),
                }
            )
        segments.append(
            {
                "id": int(item.get("id", index)),
                "text": str(item.get("text", "")).strip(),
                "start": start,
                "end": end,
                "words": words,
            }
        )
    return segments


def probe(_request: dict[str, Any]) -> dict[str, Any]:
    import torch

    # Import all optional packages so this is a real environment check.
    import edge_tts  # noqa: F401
    import pydub  # noqa: F401
    import whisper  # noqa: F401
    import whisperx  # noqa: F401

    cuda_available = bool(torch.cuda.is_available())
    return {
        "ok": True,
        "operation": "probe",
        "python": sys.version,
        "python_executable": sys.executable,
        "packages": {
            "torch": _package_version("torch"),
            "torchaudio": _package_version("torchaudio"),
            "edge-tts": _package_version("edge-tts"),
            "pydub": _package_version("pydub"),
            "openai-whisper": _package_version("openai-whisper"),
            "whisperx": _package_version("whisperx"),
        },
        "cuda_available": cuda_available,
        "cuda_version": str(torch.version.cuda or ""),
        "gpu_name": torch.cuda.get_device_name(0) if cuda_available else "",
        "ffmpeg": shutil.which("ffmpeg") or "",
        "ffprobe": shutil.which("ffprobe") or "",
    }


def edge_tts_synthesize(request: dict[str, Any]) -> dict[str, Any]:
    import aiohttp
    import edge_tts

    text = str(request.get("text", "")).strip()
    voice = str(request.get("voice", "zh-CN-XiaoxiaoNeural")).strip()
    rate = str(request.get("rate", "+0%")).strip()
    output = Path(str(request.get("output_path", ""))).expanduser().resolve()
    if not text or not voice or not output.name:
        raise ValueError("edge_tts requires text, voice, and output_path")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    async def save_with_retry() -> int:
        # On some Windows networks the Bing endpoint advertises IPv6 first but
        # resets WebSocket traffic on that route.  Edge's voice-list endpoint
        # still works, which makes the failure look intermittent.  Prefer IPv4
        # for the synthesis socket and retry only this network boundary.
        last_error: Exception | None = None
        for attempt in range(1, 5):
            output.unlink(missing_ok=True)
            try:
                async with aiohttp.TCPConnector(family=socket.AF_INET) as connector:
                    communicate = edge_tts.Communicate(
                        text=text,
                        voice=voice,
                        rate=rate,
                        connector=connector,
                        connect_timeout=30,
                        receive_timeout=90,
                    )
                    await communicate.save(str(output))
                return attempt
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_error = exc
                if attempt < 4:
                    await asyncio.sleep(attempt * 1.5)
        raise RuntimeError(f"edge-tts network failed after 4 attempts: {last_error}")

    attempts = asyncio.run(save_with_retry())
    if not output.is_file() or output.stat().st_size < 1000:
        raise RuntimeError("edge-tts did not produce a usable audio file")
    return {
        "ok": True,
        "operation": "edge_tts",
        "path": str(output),
        "provider": "edge-tts",
        "model": voice,
        "attempts": attempts,
        "duration_sec": _probe_duration(output),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


def pydub_normalize(request: dict[str, Any]) -> dict[str, Any]:
    from pydub import AudioSegment, effects

    source = Path(str(request.get("input_path", ""))).expanduser().resolve()
    output = Path(str(request.get("output_path", ""))).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"audio input does not exist: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    audio = effects.normalize(AudioSegment.from_file(source))
    audio = audio.set_channels(1).set_frame_rate(48000).set_sample_width(2)
    audio.export(output, format="wav")
    return {
        "ok": True,
        "operation": "pydub_normalize",
        "path": str(output),
        "duration_sec": _probe_duration(output),
        "sample_rate": 48000,
        "channels": 1,
    }


def whisper_transcribe(request: dict[str, Any]) -> dict[str, Any]:
    import torch
    import whisper

    source = Path(str(request.get("audio_path", ""))).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"audio input does not exist: {source}")
    model_name = str(request.get("model", "small"))
    device = str(request.get("device", "cuda"))
    language = str(request.get("language", "zh"))
    cache_dir = Path(str(request.get("cache_dir", "runtime/models"))).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
    started = time.perf_counter()
    model = whisper.load_model(model_name, device=device, download_root=str(cache_dir / "openai-whisper"))
    result = model.transcribe(
        str(source),
        language=language,
        fp16=device == "cuda",
        verbose=False,
        word_timestamps=True,
    )
    return {
        "ok": True,
        "operation": "whisper_transcribe",
        "provider": "openai-whisper",
        "model": model_name,
        "device": device,
        "language": str(result.get("language", language)),
        "text": str(result.get("text", "")).strip(),
        "segments": _normalized_segments(list(result.get("segments") or [])),
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


def whisperx_align(request: dict[str, Any]) -> dict[str, Any]:
    import torch
    import whisperx

    source = Path(str(request.get("audio_path", ""))).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"audio input does not exist: {source}")
    model_name = str(request.get("model", "small"))
    device = str(request.get("device", "cuda"))
    compute_type = str(request.get("compute_type", "float16"))
    language = str(request.get("language", "zh"))
    expected_text = str(request.get("text", "")).strip()
    cache_dir = Path(str(request.get("cache_dir", "runtime/models"))).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

    started = time.perf_counter()
    audio = whisperx.load_audio(str(source))
    model = whisperx.load_model(
        model_name,
        device,
        compute_type=compute_type,
        language=language,
        vad_method="silero",
        download_root=str(cache_dir / "whisperx"),
    )
    if expected_text:
        segments = [
            {
                "start": 0.0,
                "end": _probe_duration(source),
                "text": expected_text,
            }
        ]
        detected_language = language
    else:
        transcription = model.transcribe(audio, batch_size=4)
        segments = list(transcription.get("segments") or [])
        detected_language = str(transcription.get("language", language))

    align_model, metadata = whisperx.load_align_model(
        language_code=detected_language,
        device=device,
        model_dir=str(cache_dir / "whisperx-align"),
    )
    aligned = whisperx.align(
        segments,
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    normalized = _normalized_segments(list(aligned.get("segments") or []))
    return {
        "ok": True,
        "operation": "whisperx_align",
        "provider": "whisperx",
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "language": detected_language,
        "text": "".join(item["text"] for item in normalized),
        "segments": normalized,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }


OPERATIONS = {
    "probe": probe,
    "edge_tts": edge_tts_synthesize,
    "pydub_normalize": pydub_normalize,
    "whisper_transcribe": whisper_transcribe,
    "whisperx_align": whisperx_align,
}


def main() -> int:
    try:
        _use_system_certificate_store()
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("audio runtime request must be a JSON object")
        operation = str(request.get("operation", ""))
        handler = OPERATIONS.get(operation)
        if not handler:
            raise ValueError(f"unsupported audio runtime operation: {operation}")
        result = handler(request)
        status = 0
    except Exception as exc:  # Worker boundary: serialize every failure.
        result = {
            "ok": False,
            "operation": str(locals().get("request", {}).get("operation", "")),
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        status = 1
    print(RESULT_MARKER + json.dumps(result, ensure_ascii=False), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
