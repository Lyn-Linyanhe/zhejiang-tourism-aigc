"""Standard-library client for the isolated Python 3.11 audio runtime."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


RESULT_MARKER = "AUDIO_RUNTIME_RESULT:"


class AudioRuntimeError(RuntimeError):
    pass


class AudioRuntimeClient:
    def __init__(
        self,
        python_executable: Path,
        project_root: Path,
        cache_dir: Path,
        *,
        device: str = "cuda",
        compute_type: str = "float16",
        model: str = "small",
        allow_cpu_fallback: bool = True,
        timeout_seconds: int = 900,
    ) -> None:
        self.python_executable = python_executable.expanduser().resolve()
        self.project_root = project_root.expanduser().resolve()
        self.cache_dir = cache_dir.expanduser().resolve()
        self.device = device
        self.compute_type = compute_type
        self.model = model
        self.allow_cpu_fallback = allow_cpu_fallback
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: Any) -> "AudioRuntimeClient":
        from ..config import PROJECT_ROOT

        return cls(
            settings.audio_python,
            PROJECT_ROOT,
            settings.audio_model_cache,
            device=settings.audio_device,
            compute_type=settings.audio_compute_type,
            model=settings.whisper_model,
            allow_cpu_fallback=settings.audio_allow_cpu_fallback,
            timeout_seconds=settings.audio_timeout_seconds,
        )

    def _invoke(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self.python_executable.is_file():
            raise AudioRuntimeError(
                f"Audio runtime Python does not exist: {self.python_executable}. "
                "Run scripts/setup-audio-runtime.ps1 first."
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        backend_root = str(self.project_root / "backend")
        environment["PYTHONPATH"] = backend_root + (
            os.pathsep + environment["PYTHONPATH"]
            if environment.get("PYTHONPATH")
            else ""
        )
        environment["HF_HOME"] = str(self.cache_dir / "huggingface")
        environment["TORCH_HOME"] = str(self.cache_dir / "torch")
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                [
                    str(self.python_executable),
                    "-m",
                    "app.providers.audio_runtime_worker",
                ],
                cwd=self.project_root,
                env=environment,
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioRuntimeError(
                f"Audio runtime timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise AudioRuntimeError(f"Cannot start audio runtime: {exc}") from exc

        payload_text = next(
            (
                line[len(RESULT_MARKER) :]
                for line in reversed(completed.stdout.splitlines())
                if line.startswith(RESULT_MARKER)
            ),
            "",
        )
        if not payload_text:
            detail = (completed.stderr or completed.stdout or "no worker output").strip()
            raise AudioRuntimeError(detail[-2000:])
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise AudioRuntimeError("Audio runtime returned invalid JSON") from exc
        if completed.returncode != 0 or not payload.get("ok"):
            raise AudioRuntimeError(
                str(payload.get("error") or completed.stderr or "audio runtime failed")
            )
        return payload

    def _invoke_with_device_fallback(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._invoke(request)
            result.setdefault("fallback_used", False)
            return result
        except AudioRuntimeError as exc:
            if request.get("device") != "cuda" or not self.allow_cpu_fallback:
                raise
            fallback = dict(request)
            fallback["device"] = "cpu"
            fallback["compute_type"] = "int8"
            result = self._invoke(fallback)
            result["fallback_used"] = True
            result["fallback_reason"] = str(exc)
            return result

    def probe(self) -> dict[str, Any]:
        return self._invoke({"operation": "probe"})

    def synthesize_edge_tts(
        self,
        text: str,
        output_path: Path,
        voice: str,
        rate: str = "+0%",
        pitch: str = "+0Hz",
    ) -> dict[str, Any]:
        return self._invoke(
            {
                "operation": "edge_tts",
                "text": text,
                "output_path": str(output_path.resolve()),
                "voice": voice,
                "rate": rate,
                "pitch": pitch,
            }
        )

    def normalize(self, input_path: Path, output_path: Path) -> dict[str, Any]:
        return self._invoke(
            {
                "operation": "pydub_normalize",
                "input_path": str(input_path.resolve()),
                "output_path": str(output_path.resolve()),
            }
        )

    def transcribe(self, audio_path: Path, language: str = "zh") -> dict[str, Any]:
        return self._invoke_with_device_fallback(
            {
                "operation": "whisper_transcribe",
                "audio_path": str(audio_path.resolve()),
                "language": language,
                "model": self.model,
                "device": self.device,
                "compute_type": self.compute_type,
                "cache_dir": str(self.cache_dir),
            }
        )

    def align(
        self,
        audio_path: Path,
        text: str = "",
        language: str = "zh",
    ) -> dict[str, Any]:
        return self._invoke_with_device_fallback(
            {
                "operation": "whisperx_align",
                "audio_path": str(audio_path.resolve()),
                "text": text,
                "language": language,
                "model": self.model,
                "device": self.device,
                "compute_type": self.compute_type,
                "cache_dir": str(self.cache_dir),
            }
        )
