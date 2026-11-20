import json
import queue
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.config import settings
from backend.app.models import TaskInput
from backend.app.providers import tianyi
from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.services.audio_sync import PerShotAudioBuilder, probe_audio


class TianyiTests(unittest.TestCase):
    def test_input_accepts_local_engine(self):
        value = TaskInput.from_dict({'city': '杭州', 'theme': '漫游', 'voice_engine': 'tianyi'})
        self.assertEqual(value.voice_engine, 'tianyi')

    def test_incomplete_install_never_starts_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(tianyi, 'PROJECT_ROOT', root), patch.object(tianyi.subprocess, 'Popen') as start:
                status = tianyi.availability()
                self.assertFalse(status['available'])
                self.assertIn('runtime/tianyi/installed.json', [p.replace('\\', '/') for p in status['missing']])
                runtime = tianyi.TianyiRuntime()
                with self.assertRaisesRegex(RuntimeError, '尚未安装完整'):
                    runtime.synthesize('你好。', root/'voice.wav', 1)
                start.assert_not_called()
                runtime.close()

    def test_local_failure_does_not_substitute_other_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            builder = PerShotAudioBuilder(replace(settings, data_dir=Path(directory)), None)
            with patch.object(tianyi.runtime, 'synthesize', side_effect=RuntimeError('model unavailable')), \
                 patch('backend.app.services.audio_sync.generate_edge_tts') as edge, \
                 patch('backend.app.services.audio_sync.generate_system_speech') as system:
                with self.assertRaisesRegex(RuntimeError, 'model unavailable'):
                    builder._synthesize_cached('你好。', tianyi.VOICE_NAME, 1, 'tianyi')
                edge.assert_not_called()
                system.assert_not_called()

    def test_cache_retains_actual_source_and_rate_changes_rebuild(self):
        def synthesize(text, path, rate):
            generate_tone(path, 0.3, 440, 0.1)
            return {'model': tianyi.MODEL_ID, 'device': 'cpu'}
        with tempfile.TemporaryDirectory() as directory:
            builder = PerShotAudioBuilder(replace(settings, data_dir=Path(directory)), None)
            with patch.object(tianyi.runtime, 'synthesize', side_effect=synthesize) as synth:
                path, first = builder._synthesize_cached('测试。', tianyi.VOICE_NAME, 1, 'tianyi')
                _, cached = builder._synthesize_cached('测试。', tianyi.VOICE_NAME, 1, 'tianyi')
                _, faster = builder._synthesize_cached('测试。', tianyi.VOICE_NAME, 1.1, 'tianyi')
                self.assertEqual(synth.call_count, 2)
                self.assertEqual(synth.call_args.args[2], 1.1)
                self.assertFalse(first['cache_hit'])
                self.assertTrue(cached['cache_hit'])
                self.assertFalse(faster['cache_hit'])
                self.assertEqual(cached['provider'], 'tianyi-local')
                self.assertEqual(cached['source'], 'community')
                self.assertEqual(cached['device'], 'cpu')
                self.assertFalse(cached['pitch_applied'])
                self.assertEqual(probe_audio(path)['sample_rate'], 48000)

    def test_timeout_closes_worker_and_rejects_invalid_parameters(self):
        runtime = tianyi.TianyiRuntime()
        process = Mock()
        process.poll.return_value = None
        with patch.object(runtime, '_start'), patch.object(runtime, 'close') as close:
            runtime.process = process
            runtime.responses = Mock()
            runtime.responses.get.side_effect = queue.Empty
            close.side_effect = lambda: setattr(runtime, 'process', None)
            with self.assertRaisesRegex(RuntimeError, '240'):
                runtime.synthesize('你好。', Path('output.wav'), 1)
            close.assert_called_once()
            payload = json.loads(process.stdin.write.call_args.args[0])
            self.assertEqual(payload['text'], '你好。')
            self.assertEqual(payload['rate'], 1)
        for text, rate in [('', 1), ('字'*1001, 1), ('你好', float('nan')), ('你好', 4)]:
            with self.assertRaises(ValueError):
                runtime.synthesize(text, Path('output.wav'), rate)
        runtime.close()


if __name__ == '__main__':
    unittest.main()
