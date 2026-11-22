import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from backend.app.providers.video import CloudVideoProvider

class TokenDanceRecoveryTests(unittest.TestCase):
    def test_query_failure_preserves_remote_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=CloudVideoProvider(base_url='https://tokendance.space/gateway/minimax/v2',api_key='test-token')
            with patch.object(provider,'_tokendance_request',side_effect=[{'task_id':'remote-123'},RuntimeError('query unavailable')]), patch('backend.app.providers.video.image_to_data_url',return_value='data:image/png;base64,fixture'), patch('backend.app.providers.video.time.sleep'):
                with self.assertRaisesRegex(RuntimeError,'query unavailable'):
                    provider._generate_tokendance({'id':'shot_1'},Path(tmp)/'frame.png',tmp,'prompt',1,5,'768p')
            receipt=Path(tmp)/'shot_1.remote-task.json'
            self.assertTrue(receipt.is_file(),'remote task id must survive query failure')
            self.assertEqual(json.loads(receipt.read_text())['remote_task_id'],'remote-123')

    def test_retry_resumes_query_without_second_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=CloudVideoProvider(base_url='https://tokendance.space/gateway/minimax/v2',api_key='test-token')
            with patch.object(provider,'_tokendance_request',side_effect=[{'task_id':'remote-123'},RuntimeError('query unavailable')]), patch('backend.app.providers.video.image_to_data_url',return_value='data:image/png;base64,fixture'), patch('backend.app.providers.video.time.sleep'):
                with self.assertRaises(RuntimeError): provider._generate_tokendance({'id':'shot_1'},Path(tmp)/'frame.png',tmp,'prompt',1,5,'768p')
            with patch.object(provider,'_tokendance_request',side_effect=RuntimeError('query unavailable')) as request, patch('backend.app.providers.video.image_to_data_url',return_value='data:image/png;base64,fixture'), patch('backend.app.providers.video.time.sleep'):
                with self.assertRaises(RuntimeError): provider._generate_tokendance({'id':'shot_1'},Path(tmp)/'frame.png',tmp,'prompt',1,5,'768p')
            self.assertEqual(request.call_args.args[1],'GET','retry must query the existing task, not POST another one')

if __name__=='__main__': unittest.main()
