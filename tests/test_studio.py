"""State-machine regressions with isolated storage and entirely fake providers."""
import base64
import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.media import MediaError
from app.store import LOCK, atomic, uid
from app.studio import Studio, find


PNG = b'\x89PNG\r\n\x1a\n' + bytes(40)
MP4 = b'\x00\x00\x00\x20ftypisom' + bytes(40)
REMOTE = 'fa2d476b-3b8e-4792-a9db-c6e6ad1d717c'


class FakePlanner:
    def __init__(self):
        self.enabled = True
        self.calls = []
        self.results = []
        self.edit_result = {'summary': '仅放慢镜头运动', 'changes': {'video_prompt': '缓慢向前推进，主体轻轻抬头'}}

    def configured(self):
        return self.enabled

    def analyze(self, project, stage='plan'):
        self.calls.append((stage, copy.deepcopy(project)))
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        if stage == 'plan':
            return {'questions': [], 'plan': {'summary': '用户的明确内容', 'style': '水彩', 'audience': '家人', 'creative_notes': [], 'beats': []}}
        if stage == 'assets':
            return {'questions': [], 'assets': []}
        return {'questions': [], 'shots': []}

    def edit(self, project, target, instruction):
        self.calls.append(('edit', copy.deepcopy(target)))
        return copy.deepcopy(self.edit_result)


class FakeMedia:
    def __init__(self):
        self.submitted = []
        self.queried = []
        self.submit_error = None
        self.query_error = None
        self.query_state = 'done'
        self.query_override = None

    def available(self):
        return True

    def ffmpeg_available(self):
        return True

    def submit(self, spec, directory):
        self.submitted.append((copy.deepcopy(spec), str(directory)))
        if self.submit_error:
            raise self.submit_error
        return {'submit_id': REMOTE, 'credits': 3}

    def query(self, submit_id, directory, kind):
        self.queried.append((submit_id, str(directory), kind))
        if self.query_error:
            raise self.query_error
        if self.query_override:
            return copy.deepcopy(self.query_override)
        files = []
        if self.query_state == 'done':
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / ('result.png' if kind == 'image' else 'result.mp4')
            target.write_bytes(PNG if kind == 'image' else MP4)
            files = [str(target)]
        return {'status': self.query_state, 'files': files, 'message': '离线测试状态'}


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='idea-studio-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.media = FakeMedia()
        self.planner = FakePlanner()
        self.studio = Studio(self.root, media=self.media, planner=self.planner)
        p = self.studio.create({'source': '小树站在窗边，风吹动叶子。', 'template': 'story', 'duration': 15, 'ratio': '9:16'})
        self.pid = p['id']

    def read(self):
        return self.studio.store.read(self.pid)

    def act(self, action, **data):
        data.setdefault('expected_revision', self.read()['revision'])
        return self.studio.mutate(self.pid, action, data)

    def analyze(self, stage='plan', **data):
        data.setdefault('expected_revision', self.read()['revision'])
        return self.studio.analyze(self.pid, dict(data, stage=stage))

    def version(self, p, entity, kind='image', select=True):
        key = uid()
        rel = 'uploads/' + key + ('.png' if kind == 'image' else '.mp4')
        path = self.studio.store.folder(self.pid) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG if kind == 'image' else MP4)
        v = dict(id=key, kind=kind, url=f'/media/{self.pid}/{rel}', prompt='测试已有素材', label='离线测试', created=1)
        entity['versions'].append(v)
        if select:
            entity['selected_' + kind] = key
        return v

    def seed(self, videos=False, trial=False):
        p = self.read()
        p['plan'] = dict(summary='明确内容', style='水彩', audience='家人', creative_notes=[], beats=[])
        p['planning_stage'] = 'storyboard'
        p['phase'] = 'video'
        p['approvals'] = dict(plan=True, assets=True, storyboard=True)
        p['assets'] = [dict(id='a' + str(i), type='character', reference_role='face', name='形象' + str(i),
                            description='用户指定的形象', prompt='一个主体的清晰近景', selected_image=None,
                            approved=True, versions=[]) for i in (1, 2)]
        for asset in p['assets']:
            self.version(p, asset)
        p['shots'] = [dict(id='s' + str(i), title='镜头' + str(i), source_excerpt='', description='窗边的一棵树',
                           camera='固定镜头', action='树叶轻轻摇动', duration=5, asset_ids=['a2' if i == 2 else 'a1'],
                           image_prompt='窗边一棵树，水彩风格', video_prompt='树叶轻摇，固定镜头', selected_image=None,
                           selected_video=None, image_stale=False, video_stale=not videos, versions=[]) for i in (1, 2, 3)]
        for shot in p['shots']:
            self.version(p, shot)
            if videos:
                self.version(p, shot, 'video')
        if trial:
            if not videos:
                self.version(p, p['shots'][0], 'video')
                p['shots'][0]['video_stale'] = False
            p['trial'] = dict(shot_id='s1', version_id=p['shots'][0]['selected_video'], approved=True)
        self.studio.store.save(p)
        return p

    def quote(self, kind='shot_image', ids=None):
        return self.act('quote', kind=kind, entity_ids=['s1'] if ids is None else ids)

    def queue(self, kind='shot_image', ids=None):
        quote = self.quote(kind, ids)
        return self.act('generate', quote_id=quote['quote_id'], confirmed=True, request_id=uid())

    def apply_motion(self, shot='s1'):
        p = self.read()
        proposal = self.studio.propose(self.pid, dict(expected_revision=p['revision'], target='shot', target_id=shot, instruction='请放慢运镜'))
        return self.act('apply-edit', proposal_id=proposal['proposal_id'])

    def test_create_has_independent_defaults_and_no_paid_call(self):
        p = self.read()
        self.assertEqual(p['batch_limit'], 3)
        self.assertEqual(p['trial'], {'shot_id': None, 'version_id': None, 'approved': False})
        self.assertFalse(any(p['approvals'].values()))
        self.assertEqual(self.media.submitted, [])
        self.assertEqual(self.planner.calls, [])

    def test_three_analysis_stages_cannot_skip_prior_approval(self):
        for stage in ('assets', 'storyboard'):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                self.analyze(stage)
        self.assertEqual(self.planner.calls, [])
        p = self.analyze('plan')
        self.assertEqual(p['planning_stage'], 'plan')
        self.assertEqual(p['assets'], [])
        self.assertEqual(p['shots'], [])
        self.act('approve', stage='plan')
        self.analyze('assets')
        self.assertEqual(self.read()['planning_stage'], 'assets')
        self.act('approve', stage='assets')
        self.analyze('storyboard')
        self.assertEqual([stage for stage, _ in self.planner.calls], ['plan', 'assets', 'storyboard'])

    def test_unconfigured_analysis_never_returns_demo_or_calls_provider(self):
        self.planner.enabled = False
        with self.assertRaises(ValueError):
            self.analyze()
        self.assertIsNone(self.read()['plan'])
        self.assertEqual(self.planner.calls, [])

    def test_questions_block_approval_and_ids_are_unique_per_round(self):
        question = {'questions': [{'id': 'q1', 'question': '希望什么风格？', 'options': ['水彩', '实拍']}]}
        self.planner.results = [question, question]
        first = self.analyze()
        qid = first['questions'][0]['id']
        with self.assertRaises(ValueError):
            self.act('approve', stage='plan')
        with self.assertRaises(ValueError):
            self.analyze()
        second = self.analyze(answers={qid: '水彩'})
        self.assertNotEqual(qid, second['questions'][0]['id'])
        self.assertEqual(self.planner.calls[-1][1]['_answered'][0]['answer'], '水彩')

    def test_failed_analysis_preserves_previous_content(self):
        self.analyze()
        before = copy.deepcopy(self.read()['plan'])
        self.planner.results = [ValueError('服务返回截断')]
        with self.assertRaises(ValueError):
            self.analyze()
        after = self.read()
        self.assertEqual(after['plan'], before)
        self.assertEqual(after['text_status'], 'idle')
        self.assertIn('截断', after['text_error'])

    def test_plan_analysis_cannot_overwrite_approved_content(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.analyze('plan')
        self.assertEqual(self.planner.calls, [])

    def test_assets_approval_requires_stage_and_approved_image(self):
        p = self.seed()
        p['assets'][0]['approved'] = False
        self.studio.store.save(p)
        with self.assertRaises(ValueError):
            self.act('approve', stage='assets')

    def test_storyboard_approval_requires_current_images(self):
        p = self.seed()
        p['shots'][1]['image_stale'] = True
        self.studio.store.save(p)
        with self.assertRaises(ValueError):
            self.act('approve', stage='storyboard')

    def test_no_video_quote_without_storyboard_approval(self):
        p = self.seed(trial=True)
        p['approvals']['storyboard'] = False
        self.studio.store.save(p)
        with self.assertRaises(ValueError):
            self.quote('shot_video')

    def test_video_trial_must_be_chosen_and_only_that_shot_can_run(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.quote('shot_video')
        self.act('trial', shot_id='s1')
        with self.assertRaises(ValueError):
            self.quote('shot_video', ['s2'])
        with self.assertRaises(ValueError):
            self.quote('shot_video', ['s1', 's2'])
        self.assertEqual(self.quote('shot_video', ['s1'])['count'], 1)

    def test_trial_uses_selected_real_ten_second_shot_not_extra_five(self):
        p = self.seed()
        p['shots'][0]['duration'] = 10
        p['duration'] = 20
        self.studio.store.save(p)
        self.act('trial', shot_id='s1')
        quote = self.quote('shot_video', ['s1'])
        spec = self.read()['_quotes'][quote['quote_id']]['_specs'][0]['spec']
        self.assertEqual(spec['duration'], 10)
        self.assertEqual(self.media.submitted, [])

    def test_motion_approval_requires_finished_current_video(self):
        self.seed()
        self.act('trial', shot_id='s1')
        with self.assertRaises(ValueError):
            self.act('approve', stage='motion')
        self.queue('shot_video', ['s1'])
        self.studio.worker_once()
        self.studio.worker_once()
        p = self.act('approve', stage='motion')
        self.assertTrue(self.studio.trial_valid(p))
        self.assertEqual(len(self.media.submitted), 1)

    def test_passed_trial_reused_in_remaining_batch(self):
        self.seed(trial=True)
        quote = self.quote('shot_video', [])
        self.assertEqual([item['entity_id'] for item in quote['items']], ['s2', 's3'])
        self.assertEqual(quote['count'], 2)

    def test_batch_limit_rejects_not_silently_truncates(self):
        self.seed(trial=True)
        self.act('settings', batch_limit=1)
        with self.assertRaises(ValueError):
            self.quote('shot_video', ['s2', 's3'])
        self.assertEqual(self.read()['jobs'], [])
        self.assertEqual(self.quote('shot_video', ['s2'])['count'], 1)

    def test_settings_reject_boolean_or_out_of_range_limits(self):
        for value in (True, 0, 19, '3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.act('settings', batch_limit=value)

    def test_asset_candidate_quote_only_one_at_a_time(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.quote('asset_image', ['a1', 'a2'])
        self.assertEqual(self.quote('asset_image', ['a1'])['count'], 1)

    def test_paid_confirmation_mandatory_and_cost_unknown_visible(self):
        self.seed()
        quote = self.quote()
        self.assertFalse(quote['cost_known'])
        self.assertIn('费用未知', quote['warning'])
        with self.assertRaises(ValueError):
            self.act('generate', quote_id=quote['quote_id'], request_id=uid(), confirmed=False)
        self.assertEqual(self.read()['jobs'], [])

    def test_double_click_same_request_returns_identical_single_batch(self):
        self.seed()
        quote = self.quote()
        body = dict(quote_id=quote['quote_id'], request_id=uid(), confirmed=True)
        first = self.act('generate', **body)
        second = self.act('generate', **body)
        self.assertEqual(first['batch_id'], second['batch_id'])
        self.assertEqual(len(self.read()['jobs']), 1)
        self.studio.worker_once()
        self.assertEqual(len(self.media.submitted), 1)

    def test_concurrent_repeated_request_dispatches_only_once(self):
        self.seed()
        quote = self.quote()
        body = dict(quote_id=quote['quote_id'], request_id=uid(), confirmed=True)
        barrier = threading.Barrier(6)
        def submit():
            barrier.wait()
            return self.studio.mutate(self.pid, 'generate', body)['batch_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            batches = list(pool.map(lambda _: submit(), range(6)))
        self.assertEqual(len(set(batches)), 1)
        self.assertEqual(len(self.read()['jobs']), 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: self.studio.worker_once(), range(3)))
        self.assertEqual(len(self.media.submitted), 1)

    def test_used_quote_rejects_fresh_request_id(self):
        self.seed()
        quote = self.quote()
        self.act('generate', quote_id=quote['quote_id'], request_id=uid(), confirmed=True)
        with self.assertRaises(ValueError):
            self.act('generate', quote_id=quote['quote_id'], request_id=uid(), confirmed=True)
        self.assertEqual(len(self.read()['jobs']), 1)

    def test_changed_revision_expires_existing_quote(self):
        self.seed()
        quote = self.quote()
        self.act('settings', batch_limit=2)
        with self.assertRaises(ValueError):
            self.act('generate', quote_id=quote['quote_id'], request_id=uid(), confirmed=True)

    def test_uncertain_submission_restart_never_resubmits(self):
        self.seed()
        self.queue(ids=['s1', 's2'])
        self.media.submit_error = MediaError('请求超时', uncertain=True)
        self.studio.worker_once()
        self.assertEqual(self.read()['jobs'][0]['status'], 'uncertain')
        restarted = Studio(self.root, self.media, self.planner)
        restarted.startup_recover()
        restarted.worker_once()
        restarted.worker_once()
        self.assertEqual(len(self.media.submitted), 1)
        self.assertEqual(self.media.queried, [])

    def test_crashed_submitting_record_becomes_uncertain(self):
        self.seed()
        self.queue()
        p = self.read()
        p['jobs'][0]['status'] = 'submitting'
        self.studio.store.save(p)
        self.studio.startup_recover()
        self.studio.worker_once()
        self.assertEqual(self.read()['jobs'][0]['status'], 'uncertain')
        self.assertEqual(self.media.submitted, [])

    def test_failed_submit_pauses_following_jobs(self):
        self.seed()
        self.queue(ids=['s1', 's2'])
        self.media.submit_error = MediaError('平台明确拒绝', uncertain=False)
        self.studio.worker_once()
        self.studio.worker_once()
        p = self.read()
        self.assertEqual([j['status'] for j in p['jobs']], ['failed', 'queued'])
        self.assertTrue(p['paused'])
        self.assertEqual(len(self.media.submitted), 1)

    def test_failed_remote_result_pauses_following_jobs(self):
        self.seed()
        self.queue(ids=['s1', 's2'])
        self.studio.worker_once()
        self.media.query_state = 'failed'
        self.studio.worker_once()
        self.studio.worker_once()
        self.assertTrue(self.read()['paused'])
        self.assertEqual(len(self.media.submitted), 1)
        self.assertEqual(self.read()['jobs'][1]['status'], 'queued')

    def test_query_error_blocks_further_spending(self):
        self.seed()
        self.queue(ids=['s1', 's2'])
        self.studio.worker_once()
        self.media.query_error = MediaError('查询超时')
        self.studio.worker_once()
        self.studio.worker_once()
        self.assertIn('query_error', self.read()['jobs'][0])
        self.assertEqual(len(self.media.submitted), 1)

    def test_manual_recovery_only_queries_bound_original_id(self):
        self.seed()
        self.queue()
        self.media.submit_error = MediaError('超时', uncertain=True)
        self.studio.worker_once()
        self.act('recover', job_id=self.read()['jobs'][0]['id'], submit_id=REMOTE)
        self.studio.worker_once()
        self.assertEqual(self.media.queried[0][0], REMOTE)
        self.assertEqual(len(self.media.submitted), 1)
        self.assertEqual(self.read()['jobs'][0]['status'], 'done')

    def test_cancel_queued_never_claims_running_task_canceled(self):
        self.seed()
        self.queue(ids=['s1', 's2'])
        self.studio.worker_once()
        p = self.act('pause', paused=True, cancel_queued=True)
        self.assertEqual([j['status'] for j in p['jobs']], ['waiting', 'canceled'])
        self.assertEqual(len(self.media.submitted), 1)

    def test_motion_only_edit_keeps_image_and_storyboard_revokes_own_trial(self):
        p = self.seed(videos=True, trial=True)
        old_image = p['shots'][0]['selected_image']
        changed = self.apply_motion()
        self.assertFalse(changed['shots'][0]['image_stale'])
        self.assertEqual(changed['shots'][0]['selected_image'], old_image)
        self.assertTrue(changed['shots'][0]['video_stale'])
        self.assertTrue(changed['approvals']['storyboard'])
        self.assertFalse(changed['trial']['approved'])
        self.assertFalse(changed['shots'][1]['video_stale'])

    def test_unrelated_motion_edit_keeps_passed_trial(self):
        self.seed(videos=True, trial=True)
        changed = self.apply_motion('s2')
        self.assertTrue(changed['trial']['approved'])
        self.assertTrue(self.studio.trial_valid(changed))
        self.assertTrue(changed['shots'][1]['video_stale'])

    def test_asset_version_change_invalidates_only_dependent_shots(self):
        p = self.seed(videos=True, trial=True)
        version = self.version(p, p['assets'][1], select=False)
        self.studio.store.save(p)
        changed = self.act('select', entity='asset', entity_id='a2', kind='image', version_id=version['id'])
        self.assertFalse(changed['shots'][0]['image_stale'])
        self.assertTrue(changed['shots'][1]['image_stale'])
        self.assertFalse(changed['shots'][2]['image_stale'])
        self.assertTrue(changed['trial']['approved'])

    def test_changing_trial_selected_video_requires_reapproval(self):
        p = self.seed(videos=True, trial=True)
        v = self.version(p, p['shots'][0], 'video', select=False)
        self.studio.store.save(p)
        changed = self.act('select', entity='shot', entity_id='s1', kind='video', version_id=v['id'])
        self.assertFalse(changed['trial']['approved'])
        self.assertFalse(self.studio.trial_valid(changed))

    def test_style_reference_is_real_file_with_role_and_no_duplicate(self):
        self.seed()
        self.act('style-reference', asset_id='a2')
        p = self.read()
        spec = self.studio.spec(p, 'shot_image', p['shots'][0])
        self.assertEqual(len(spec['references']), 2)
        self.assertIn('不增加其中人物或物体', spec['prompt'])
        self.assertTrue(all(Path(path).is_file() for path in spec['references']))
        same = self.studio.spec(p, 'shot_image', p['shots'][1])
        self.assertEqual(len(same['references']), 1)
        self.assertTrue(all(s['image_stale'] for s in p['shots']))

    def test_asset_variant_preserves_original_and_remaps_only_selected_shots(self):
        original = self.seed(videos=True, trial=True)
        changed = self.act('asset-variant', asset_id='a1', shot_ids=['s3'], name='夏日服装', description='明确浅蓝衣服', prompt='原角色穿浅蓝衣服')
        self.assertEqual(changed['assets'][0], original['assets'][0])
        variant = changed['assets'][-1]
        self.assertEqual(variant['source_asset_id'], 'a1')
        self.assertEqual(variant['versions'], [])
        self.assertEqual(changed['shots'][2]['asset_ids'], [variant['id']])
        self.assertEqual(changed['shots'][0]['asset_ids'], ['a1'])
        self.assertFalse(changed['shots'][0]['image_stale'])
        self.assertTrue(changed['trial']['approved'])

    def test_restore_retains_newer_versions_and_resets_trial(self):
        p = self.seed(videos=True, trial=True)
        self.studio.store.snapshot(p, '之前的镜头')
        snapshot = p['history'][-1]['id']
        old = p['shots'][0]['selected_video']
        newer = self.version(p, p['shots'][0], 'video')
        p['shots'][0]['title'] = '后来改名'
        self.studio.store.save(p)
        restored = self.act('restore', version_id=snapshot)
        self.assertEqual(restored['shots'][0]['selected_video'], old)
        self.assertIn(newer['id'], {v['id'] for v in restored['shots'][0]['versions']})
        self.assertFalse(any(restored['approvals'].values()))
        self.assertFalse(restored['trial']['approved'])
        self.assertTrue(self.studio.version_path(restored, newer).is_file())

    def test_upload_requires_explicit_identity_selection(self):
        p = self.seed()
        p['assets'][0]['selected_image'] = None
        p['assets'][0]['approved'] = False
        self.studio.store.save(p)
        uploaded = self.act('upload', asset_id='a1', data=base64.b64encode(PNG).decode(), name='my.png')
        self.assertIsNone(uploaded['assets'][0]['selected_image'])
        self.assertFalse(uploaded['assets'][0]['approved'])
        v = uploaded['assets'][0]['versions'][-1]
        confirmed = self.act('select', entity='asset', entity_id='a1', kind='image', version_id=v['id'])
        self.assertTrue(confirmed['assets'][0]['approved'])

    def test_private_files_cannot_be_served_and_cross_project_refs_rejected(self):
        p = self.seed()
        for path in ('project.json', '../../.local/config.json', 'history/anything.json', '../other/project.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.studio.media_path(self.pid, path)
        other = self.studio.create({'source': '另一项目', 'duration': 15})
        version = copy.deepcopy(p['assets'][0]['versions'][0])
        with self.assertRaises(ValueError):
            self.studio.version_path(other, version)

    def test_download_outside_job_directory_not_registered(self):
        self.seed()
        self.queue()
        self.studio.worker_once()
        outside = self.root / 'foreign.png'
        outside.write_bytes(PNG)
        self.media.query_override = {'status': 'done', 'files': [str(outside)]}
        self.studio.worker_once()
        job = self.read()['jobs'][0]
        self.assertNotEqual(job['status'], 'done')
        self.assertIn('query_error', job)

    def test_public_project_omits_internal_specs_quotes_and_answers_history(self):
        self.seed()
        result = self.queue()
        self.assertFalse(any(k.startswith('_') for k in result['project']))
        self.assertFalse(any(k.startswith('_') for job in result['jobs'] for k in job))
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_stale_revision_rejected_without_modification(self):
        self.seed()
        before = self.read()
        with self.assertRaises(ValueError):
            self.act('remove-shot', shot_id='s3', expected_revision=0)
        self.assertEqual(self.read()['shots'], before['shots'])

    def test_corrupt_project_does_not_stop_other_project_listing(self):
        bad = self.studio.store.folder('damaged') / 'project.json'
        atomic(bad, [])
        rows = self.studio.store.list()
        self.assertIn(self.pid, {row['id'] for row in rows})
        self.assertEqual(find(rows, 'damaged')['phase'], 'damaged')

    def test_uncertain_task_cannot_silently_replace_changed_prompt_later(self):
        self.seed()
        self.queue()
        self.media.submit_error = MediaError('超时', uncertain=True)
        self.studio.worker_once()
        # Either block edits while unresolved, or archive recovered output without
        # activating it. This implementation chooses the safer first alternative.
        with self.assertRaises(ValueError):
            self.apply_motion()
        self.assertEqual(self.planner.calls, [])


if __name__ == '__main__':
    unittest.main()
