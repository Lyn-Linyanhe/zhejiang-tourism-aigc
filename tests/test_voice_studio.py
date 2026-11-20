import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from backend.app.config import settings
from backend.app.models import Shot, Task, TaskInput
from backend.app.services.audio_sync import PerShotAudioBuilder, probe_audio
from backend.app.services.orchestrator import Orchestrator
from backend.app.services.voice_studio import plan_segments, split_sentences, phrase_parameters, validate_clip
from backend.app.providers.media_ffmpeg import generate_tone
from backend.app.storage import Storage


class VoiceStudioTests(unittest.TestCase):
    def test_preserves_comma_context_decimal_and_quotes(self):
        text = '走过断桥，看看湖面、远山。这里有3.5公里，去看看吗？”'
        self.assertEqual(split_sentences(text), ['走过断桥，看看湖面、远山。', '这里有3.5公里，去看看吗？”'])
        self.assertEqual(''.join(split_sentences(text)), text)

    def test_caption_mismatch_keeps_whole_text(self):
        self.assertEqual(plan_segments('甲。乙。', '画面摘要'), [('甲。乙。', '画面摘要')])

    def test_zero_strength_and_manual_overrides(self):
        shot = Shot('s', 1, 4, '', '', '你好！', '', voice_rate_scale=1.1, voice_pitch_offset=3)
        params = phrase_parameters(shot.narration, style='bright', strength=0, rate=1, pitch=2, clip=shot)
        self.assertEqual(params['rate'], 1.1)
        self.assertEqual(params['pitch_hz'], 5)
        shot.voice_style = 'neutral'
        self.assertEqual(phrase_parameters(shot.narration, style='bright', strength=1, rate=1, pitch=2, clip=shot)['pitch_hz'], 5)

    def test_question_and_statement_receive_different_small_offsets(self):
        shot = Shot('s', 1, 4, '', '', '', '')
        args = dict(style='natural', strength=0.5, rate=1, pitch=0, clip=shot)
        self.assertGreater(phrase_parameters('一起去吗？', **args)['pitch_hz'], phrase_parameters('一起去。', **args)['pitch_hz'])

    def test_reject_invalid_clip_settings(self):
        for raw in ({'voice_rate_scale': float('nan')}, {'voice_pitch_offset': 90}, {'voice_pause_after': -1}, {'voice_style': 'unknown'}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_clip(raw)

    def test_old_inputs_load_with_defaults(self):
        task_input = TaskInput.from_dict({'city': '杭州', 'theme': '漫游'})
        self.assertEqual(task_input.voice_engine, 'configured')
        self.assertEqual(task_input.voice_style, 'natural')

    def test_old_xstudio_source_migrates_to_generic_upload(self):
        value = TaskInput.from_dict({'city':'杭州', 'theme':'漫游', 'voice_engine':'xstudio', 'voice':'洛天依（X Studio 导入）'})
        self.assertEqual(value.voice_engine, 'upload')
        self.assertEqual(value.voice, '上传配音音频')

    def test_upload_never_silently_substitutes_voice(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(settings, data_dir=Path(directory))
            builder = PerShotAudioBuilder(config, None)
            shot = Shot('s',1,4,'','','你好。','你好。')
            with patch.object(builder, '_synthesize_cached') as synthesize, self.assertRaisesRegex(ValueError, '尚未绑定配音音频'):
                builder.build('t', [shot], voice='上传配音音频', rate=1, target_duration=10, force_provider='upload')
            synthesize.assert_not_called()

    def test_imported_audio_and_custom_gap_keep_measured_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root/'import.wav'
            generate_tone(audio, 0.6, 440, 0.1)
            config = replace(settings, data_dir=root)
            shots = [Shot(f's{i}',i,1,'','','测试','测试',audio_local_path=str(audio),voice_pause_after=0.4,voice_pitch_offset=15) for i in (1,2)]
            result = PerShotAudioBuilder(config,None).build('t',shots,voice='上传配音音频',rate=1,target_duration=5,force_provider='upload')
            self.assertAlmostEqual(result.cues[1].start-result.cues[0].end,0.4,places=3)
            self.assertAlmostEqual(result.total_duration,sum(s.duration_sec for s in shots),places=3)
            self.assertFalse(result.provider_trace[0]['prosody']['applied'])
            self.assertEqual(probe_audio(Path(result.voice_path))['sample_rate'],48000)

    def test_unsaved_preview_and_plan_do_not_mutate_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = replace(settings, data_dir=root)
            storage = Storage(root/'test.sqlite3')
            task = Task(id='test', input=TaskInput.from_dict({'city':'杭州','theme':'漫游'}), status='draft',
                        shots=[Shot('s',1,30,'','','旧旁白。','旧旁白。')])
            storage.save_task(task)
            before = copy.deepcopy(storage.get_task('test'))
            orchestrator = Orchestrator(config, storage)
            payload = {'input':{'voice_style':'warm'}, 'shots':[{'id':'s','narration':'新旁白，连着读。','subtitle':'新旁白，连着读。','voice_pitch_offset':2}]}
            plan = orchestrator.voice_preview('test',payload,synthesize=False)
            self.assertEqual(plan['clips'][0]['phrases'][0]['text'], '新旁白，连着读。')
            audio = root/'tone.wav'
            generate_tone(audio,0.7,440,0.1)
            with patch.object(orchestrator.audio_builder, '_synthesize_cached', return_value=(audio, {'provider':'test-tone','model':'test-only'})):
                result = orchestrator.voice_preview('test',payload,synthesize=True)
            self.assertEqual(result['timing'],'measured')
            self.assertIn('/voice-previews/',result['audio_url'])
            self.assertEqual(storage.get_task('test'),before)
            with self.assertRaises(ValueError):
                orchestrator.voice_preview('test',{'shot_id':'missing'},synthesize=False)

    def test_explicit_neural_failure_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as directory:
            builder = PerShotAudioBuilder(replace(settings,data_dir=Path(directory)),None)
            with patch('backend.app.services.audio_sync.generate_edge_tts',side_effect=RuntimeError('network unavailable')), patch('backend.app.services.audio_sync.generate_system_speech') as system:
                with self.assertRaisesRegex(RuntimeError,'network unavailable'):
                    builder._synthesize_cached('测试','晓晓·温暖女声',1,'edge')
                system.assert_not_called()


if __name__ == '__main__':
    unittest.main()
