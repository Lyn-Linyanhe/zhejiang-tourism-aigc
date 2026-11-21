import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.app.config import Settings
from backend.app.services.provider_settings import public_settings, validate_changes, write_dotenv


class ProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(
            Settings(), llm_base_url="https://example.test/v1", llm_api_key="fake-existing-key",
        )

    def test_status_does_not_include_key_and_blank_preserves_it(self):
        status = public_settings(self.settings)
        self.assertNotIn("fake-existing-key", json.dumps(status))
        key = next(field for group in status["groups"] for field in group["fields"]
                   if field["name"] == "LLM_API_KEY")
        self.assertTrue(key["configured"])
        self.assertNotIn("value", key)
        editable = public_settings(self.settings, include_keys=True)
        self.assertTrue(any(field.get("value") == "fake-existing-key"
                            for group in editable["groups"] for field in group["fields"]))
        with patch("backend.app.services.provider_settings.INHERITED_ENV", frozenset()):
            self.assertEqual(validate_changes(self.settings, {
                "LLM_BASE_URL": "https://example.test/v2/",
                "LLM_API_KEY": "",
            }), {"LLM_BASE_URL": "https://example.test/v2"})

    def test_rejects_unknown_fields_unsafe_urls_and_unintentional_key_reuse(self):
        with patch("backend.app.services.provider_settings.INHERITED_ENV", frozenset({"IMAGE_API_KEY"})):
            for submitted in (
                {"DATA_DIR": "/somewhere"},
                {"IMAGE_API_KEY": "new"},
                {"LLM_BASE_URL": "https://user:token@example.test/v1"},
                {"LLM_BASE_URL": "https://example.test/v1?key=secret"},
                {"LLM_BASE_URL": "https://another.test/v1"},
                {"TTS_VOICE_CATALOG": "[]"},
            ):
                with self.subTest(submitted=list(submitted)), self.assertRaises(ValueError):
                    validate_changes(self.settings, submitted)
            self.assertEqual(validate_changes(self.settings, {
                "LLM_BASE_URL": "https://another.test/v1", "LLM_API_KEY": "new-key",
            }), {"LLM_BASE_URL": "https://another.test/v1", "LLM_API_KEY": "new-key"})

    def test_updates_dotenv_without_disturbing_other_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("# service\nLLM_MODEL=old\nDATA_DIR=runtime\nLLM_MODEL=duplicate\n", encoding="utf-8")
            write_dotenv(path, {"LLM_MODEL": "new-model", "TTS_VOICE_CATALOG": '{"guide":"导览"}'})
            self.assertEqual(path.read_text(encoding="utf-8").splitlines(), [
                "# service", 'LLM_MODEL="new-model"', "DATA_DIR=runtime", 'TTS_VOICE_CATALOG="{"guide":"导览"}"',
            ])
            self.assertEqual(list(Path(folder).iterdir()), [path])
