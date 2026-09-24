import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from app.planner import TextService, validate_stage


def plan():
    return {'questions': [], 'plan': {'summary': '展示用户提供的红色杯子', 'style': '用户指定写实',
            'audience': '用户指定朋友', 'creative_notes': [],
            'beats': [{'title': '外观', 'content': '看见杯子', 'source_excerpt': '红色杯子'}]}}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = TextService(Path(self.temp.name))
        self.p = {'source': '红色杯子在桌上。', 'template': 'product', 'duration': 15, 'ratio': '9:16',
                  'plan': None, 'assets': [], 'questions': [], 'answers': {}}

    def configured(self):
        self.service.save({'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'test-secret'})

    def complete(self, data):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        opener = MagicMock()
        opener.open.return_value = response
        return patch('urllib.request.build_opener', return_value=opener), opener

    def test_unconfigured_does_not_call_network(self):
        with patch('urllib.request.build_opener') as network:
            with self.assertRaisesRegex(ValueError, '尚未配置'):
                self.service.complete('JSON', {})
            network.assert_not_called()

    def test_config_never_returns_secret(self):
        self.configured()
        self.assertNotIn('test-secret', json.dumps(self.service.public()))
        self.assertTrue(self.service.public()['key_configured'])

    def test_changed_endpoint_does_not_reuse_secret(self):
        self.configured()
        with self.assertRaisesRegex(ValueError, '旧密钥'):
            self.service.save({'base_url': 'https://another.invalid/v1', 'model': 'test'})
        self.assertEqual(self.service.config()['base_url'], 'https://example.invalid/v1')

    def test_same_endpoint_keeps_key_but_clear_removes(self):
        self.configured()
        self.service.save({'base_url': 'https://example.invalid/v1', 'model': 'test2', 'api_key': ''})
        self.assertTrue(self.service.configured())
        self.service.save({'base_url': 'https://another.invalid/v1', 'model': 'test', 'clear_key': True})
        self.assertFalse(self.service.configured())

    def test_unsafe_config_urls_rejected(self):
        for url in ('http://example.com/v1', 'https://user:secret@example.com/v1', 'https://example.com/?key=secret', 'file:///tmp/key'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.service.save({'base_url': url, 'model': 'test'})

    def test_loopback_config_allowed(self):
        self.service.save({'base_url': 'http://127.0.0.1:1234/v1', 'model': 'test', 'api_key': 'local'})
        self.assertTrue(self.service.configured())

    def test_json_request_and_complete_response(self):
        self.configured()
        self.service.save({'base_url': 'https://example.invalid/v1', 'model': 'test', 'json_mode': True})
        ctx, opener = self.complete({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(plan())}}]})
        with ctx:
            self.assertEqual(self.service.complete('Return JSON', {}), plan())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://example.invalid/v1/chat/completions')
        self.assertEqual(json.loads(request.data)['response_format'], {'type': 'json_object'})
        self.assertEqual(opener.open.call_count, 1)

    def test_truncation_and_refusal_are_not_valid_plans(self):
        self.configured()
        for choice in ({'finish_reason': 'length', 'message': {'content': '{}'}},
                       {'finish_reason': 'stop', 'message': {'refusal': 'no', 'content': '{}'}},
                       {'finish_reason': 'content_filter', 'message': {'content': '{}'}}):
            ctx, opener = self.complete({'choices': [choice]})
            with ctx, self.assertRaises(ValueError):
                self.service.complete('JSON', {})
            self.assertEqual(opener.open.call_count, 1)

    def test_bad_json_no_retry(self):
        self.configured()
        ctx, opener = self.complete({'choices': [{'finish_reason': 'stop', 'message': {'content': '{broken'}}]})
        with ctx, self.assertRaises(ValueError):
            self.service.complete('JSON', {})
        self.assertEqual(opener.open.call_count, 1)

    def test_http_error_not_echoed(self):
        self.configured()
        opener = MagicMock()
        opener.open.side_effect = urllib.error.HTTPError('secret-url', 401, 'test-secret', {}, None)
        with patch('urllib.request.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as error:
                self.service.complete('JSON', {})
        self.assertNotIn('test-secret', str(error.exception))
        self.assertIn('401', str(error.exception))

    def test_plan_does_not_create_shots(self):
        output = validate_stage(plan(), self.p, 'plan')
        self.assertNotIn('shots', output)
        self.assertNotIn('assets', output)

    def test_plan_rejects_skipping_ahead(self):
        data = plan()
        data['shots'] = [{'id': 's1'}]
        with self.assertRaises(ValueError):
            validate_stage(data, self.p, 'plan')

    def test_fabricated_source_excerpt_rejected(self):
        data = plan()
        data['plan']['beats'][0]['source_excerpt'] = '蓝色杯子'
        with self.assertRaises(ValueError):
            validate_stage(data, self.p, 'plan')

    def test_questions_max_three_and_no_duplicates(self):
        for questions in ([{'id': 'same', 'question': '风格？'}] * 2,
                          [{'id': str(i), 'question': '问题'} for i in range(4)], ['not-object']):
            with self.assertRaises(ValueError):
                validate_stage({'questions': questions}, self.p, 'plan')

    def test_questions_preserve_choices(self):
        questions = [{'id': 'style', 'question': '视觉风格？', 'options': ['写实', '插画']}]
        self.assertEqual(validate_stage({'questions': questions}, self.p, 'plan'), {'questions': questions})

    def test_no_mandatory_character_or_style_asset(self):
        self.assertEqual(validate_stage({'assets': []}, self.p, 'assets'), {'questions': [], 'assets': []})

    def test_asset_validation_separate_reference_types(self):
        data = {'assets': [{'id': 'face', 'type': 'character', 'name': '面部', 'description': '已确认的脸',
                            'reference_role': 'face', 'prompt': '单张面部近景'}]}
        asset = validate_stage(data, self.p, 'assets')['assets'][0]
        self.assertFalse(asset['approved'])
        self.assertIsNone(asset['selected_image'])

    def shot(self):
        return {'id': 's1', 'title': '镜头', 'description': '杯子', 'camera': '固定', 'action': '轻转',
                'image_prompt': '杯子首帧', 'video_prompt': '杯子轻转', 'duration': 5, 'asset_ids': [], 'source_excerpt': '红色杯子'}

    def test_shot_sum_and_reference_validation(self):
        shots = [dict(self.shot(), id='s' + str(i)) for i in range(3)]
        output = validate_stage({'shots': shots}, self.p, 'storyboard')
        self.assertEqual(len(output['shots']), 3)
        bad = copy.deepcopy(shots)
        bad[0]['duration'] = 10
        with self.assertRaises(ValueError):
            validate_stage({'shots': bad}, self.p, 'storyboard')
        bad = copy.deepcopy(shots)
        bad[0]['asset_ids'] = ['unapproved']
        with self.assertRaises(ValueError):
            validate_stage({'shots': bad}, self.p, 'storyboard')


if __name__ == '__main__':
    unittest.main()
