import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.config import PROJECT_ROOT, settings
from backend.app.providers.audio_runtime import AudioRuntimeClient, AudioRuntimeError


class AudioRuntimeClientTests(unittest.TestCase):
    def test_configured_paths_are_relocation_safe_and_absolute(self):
        self.assertTrue(settings.audio_python.is_absolute())
        self.assertTrue(settings.audio_model_cache.is_absolute())
        self.assertTrue(settings.audio_python.is_relative_to(PROJECT_ROOT))
        self.assertTrue(settings.audio_model_cache.is_relative_to(PROJECT_ROOT))

    def test_missing_runtime_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = AudioRuntimeClient(
                root / "missing-python.exe",
                root,
                root / "models",
            )
            with self.assertRaisesRegex(AudioRuntimeError, "setup-audio-runtime"):
                client.probe()

    def test_cuda_failure_can_fall_back_to_cpu(self):
        client = AudioRuntimeClient(
            Path("python.exe"),
            PROJECT_ROOT,
            PROJECT_ROOT / "runtime" / "models",
            allow_cpu_fallback=True,
        )
        with patch.object(
            client,
            "_invoke",
            side_effect=[AudioRuntimeError("CUDA out of memory"), {"ok": True, "device": "cpu"}],
        ) as invoke:
            result = client._invoke_with_device_fallback(
                {"operation": "whisper_transcribe", "device": "cuda", "compute_type": "float16"}
            )
        self.assertEqual(invoke.call_args_list[-1].args[0]["compute_type"], "int8")
        self.assertTrue(result["fallback_used"])
        self.assertIn("CUDA out of memory", result["fallback_reason"])


if __name__ == "__main__":
    unittest.main()
