from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, settings
from app.providers.audio_runtime import AudioRuntimeClient


SAMPLE_TEXT = "杭州西湖的晨光掠过苏堤，宋韵在水岸与街巷之间缓缓展开。"


def validate_timeline(segments: list[dict[str, Any]], duration: float) -> dict[str, int]:
    if not segments:
        raise RuntimeError("alignment returned no segments")
    previous = 0.0
    word_count = 0
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        if start < previous - 0.05 or end < start or end > duration + 0.25:
            raise RuntimeError(f"invalid segment timeline: {start:.3f}-{end:.3f}/{duration:.3f}")
        previous = max(previous, end)
        word_previous = start
        for word in segment.get("words") or []:
            word_start = float(word["start"])
            word_end = float(word["end"])
            if word_start < word_previous - 0.05 or word_end < word_start or word_end > duration + 0.25:
                raise RuntimeError(
                    f"invalid word timeline: {word_start:.3f}-{word_end:.3f}/{duration:.3f}"
                )
            word_previous = max(word_previous, word_end)
            word_count += 1
    return {"segments": len(segments), "words": word_count}


def main() -> int:
    settings.ensure_dirs()
    diagnostics = settings.data_dir / "diagnostics"
    work_dir = diagnostics / "audio-runtime-smoke"
    work_dir.mkdir(parents=True, exist_ok=True)
    report_path = diagnostics / "audio-runtime-report.json"
    client = AudioRuntimeClient.from_settings(settings)
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "project_root": str(PROJECT_ROOT),
        "model": settings.whisper_model,
        "requested_device": settings.audio_device,
        "complete_gpu_usable": False,
        "steps": {},
    }
    started = time.perf_counter()
    try:
        report["steps"]["probe"] = client.probe()
        sample_mp3 = work_dir / "edge-tts-sample.mp3"
        report["steps"]["edge_tts"] = client.synthesize_edge_tts(
            SAMPLE_TEXT,
            sample_mp3,
            "zh-CN-XiaoxiaoNeural",
        )
        normalized_wav = work_dir / "edge-tts-sample-48k.wav"
        normalized = client.normalize(sample_mp3, normalized_wav)
        report["steps"]["pydub"] = normalized
        duration = float(normalized["duration_sec"])

        transcribed = client.transcribe(normalized_wav, "zh")
        transcribed["timeline"] = validate_timeline(transcribed["segments"], duration)
        report["steps"]["openai_whisper"] = transcribed

        aligned = client.align(normalized_wav, SAMPLE_TEXT, "zh")
        aligned["timeline"] = validate_timeline(aligned["segments"], duration)
        report["steps"]["whisperx"] = aligned

        probe = report["steps"]["probe"]
        report["complete_gpu_usable"] = bool(
            probe.get("cuda_available")
            and "RTX 4060" in str(probe.get("gpu_name", ""))
            and transcribed.get("device") == "cuda"
            and aligned.get("device") == "cuda"
            and not transcribed.get("fallback_used")
            and not aligned.get("fallback_used")
        )
        if not report["complete_gpu_usable"]:
            raise RuntimeError("audio packages work, but complete RTX 4060 GPU acceptance did not pass")
        report["status"] = "passed"
        return_code = 0
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["error_type"] = type(exc).__name__
        return_code = 1
    report["elapsed_sec"] = round(time.perf_counter() - started, 3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
