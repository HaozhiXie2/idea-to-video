"""Offline media contract checks. No test can submit a real Dreamina job."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.media import MediaEngine, MediaError, _parse_json


TASK = 'fa2d476b-3b8e-4792-a9db-c6e6ad1d717c'
PNG = b'\x89PNG\r\n\x1a\n' + b'fixture-not-decoded-by-mocked-cli'
MP4 = b'\x00\x00\x00\x20ftypisom' + b'fixture-not-decoded-by-mocked-ffmpeg'


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='idea-media-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.engine = MediaEngine(self.root)
        self.engine.cli.parent.mkdir()
        self.engine.cli.write_bytes(b'offline-test-placeholder')
        self.directory = self.root / 'data' / 'project' / 'jobs' / 'one'
        self.reference = self.root / 'reference.png'
        self.reference.write_bytes(PNG)
        self.spec = {'kind': 'image', 'prompt': '一个人在花园中', 'ratio': '9:16', 'model': '5.0', 'references': []}

    @staticmethod
    def result(value, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(value), '')

    def test_text_to_image_is_async_one_candidate_no_retry(self):
        with patch('app.media.subprocess.run', return_value=self.result({'submit_id': TASK, 'credit_count': 3})) as run:
            result = self.engine.submit(self.spec, self.directory)
        self.assertEqual(result, {'submit_id': TASK, 'credits': 3})
        self.assertEqual(run.call_count, 1)
        args = run.call_args.args[0]
        self.assertEqual(args[1], 'text2image')
        self.assertIn('--generate_num=1', args)
        self.assertIn('--poll=0', args)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_image_references_are_uploaded_as_explicit_images(self):
        self.spec['references'] = [str(self.reference)]
        with patch('app.media.subprocess.run', return_value=self.result({'submit_id': TASK})) as run:
            self.engine.submit(self.spec, self.directory)
        args = run.call_args.args[0]
        self.assertEqual(args[1], 'image2image')
        self.assertIn('--images=' + str(self.reference), args)

    def test_video_requires_one_reference_and_known_duration(self):
        self.spec.update(kind='video', model='seedance2.0fast_vip', duration=5, references=[str(self.reference)])
        with patch('app.media.subprocess.run', return_value=self.result({'submit_id': TASK})) as run:
            self.engine.submit(self.spec, self.directory)
        args = run.call_args.args[0]
        self.assertEqual(args[1], 'image2video')
        self.assertIn('--duration=5', args)
        self.assertIn('--image=' + str(self.reference), args)

    def test_invalid_parameters_do_not_start_any_process(self):
        changes = [{'duration': True, 'kind': 'video', 'model': 'seedance2.0fast_vip', 'references': [str(self.reference)]},
                   {'kind': 'video', 'duration': 5, 'model': 'seedance2.0fast_vip'},
                   {'ratio': 'invalid'}, {'model': '--help'}, {'prompt': 'x'},
                   {'references': [str(self.reference), str(self.reference)]}]
        with patch('app.media.subprocess.run') as run:
            for change in changes:
                with self.subTest(change=change), self.assertRaises(MediaError) as raised:
                    self.engine.submit(dict(self.spec, **change), self.directory)
                self.assertFalse(raised.exception.uncertain)
        run.assert_not_called()

    def test_home_is_project_local_without_mutating_process_environment(self):
        before = dict(os.environ)
        with patch.dict(os.environ, {'DREAMINA_TOKEN': 'secret', 'JIMENG_HOME': 'old-home'}):
            current_before = dict(os.environ)
            env = self.engine.cli_environment()
            self.assertEqual(dict(os.environ), current_before)
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn('DREAMINA_TOKEN', env)
        self.assertNotIn('JIMENG_HOME', env)
        for key in ['HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME']:
            self.assertTrue(Path(env[key]).is_relative_to(self.root / '.local/dreamina-home'))
        other = MediaEngine(self.root / 'independent')
        self.assertNotEqual(other.cli_environment()['USERPROFILE'], env['USERPROFILE'])

    def test_cli_subprocess_receives_isolated_home(self):
        with patch('app.media.subprocess.run', return_value=self.result({'submit_id': TASK})) as run:
            self.engine.submit(self.spec, self.directory)
        self.assertEqual(run.call_args.kwargs['env']['USERPROFILE'], str(self.root / '.local/dreamina-home'))
        self.assertEqual(run.call_args.kwargs['cwd'], str(self.root))

    def test_timeout_is_uncertain_and_not_retried(self):
        with patch('app.media.subprocess.run', side_effect=subprocess.TimeoutExpired('dreamina', 120)) as run:
            with self.assertRaises(MediaError) as raised:
                self.engine.submit(self.spec, self.directory)
        self.assertTrue(raised.exception.uncertain)
        self.assertEqual(run.call_count, 1)

    def test_bad_json_and_missing_id_are_uncertain(self):
        bad_values = ['warning\n' + json.dumps({'submit_id': TASK}), '[]', '{}', '{"submit_id":"abc","submit_id":"def"}', '{"submit_id":NaN}']
        for value in bad_values:
            with self.subTest(value=value), patch('app.media.subprocess.run', return_value=subprocess.CompletedProcess([], 0, value, 'sensitive-log')) as run:
                with self.assertRaises(MediaError) as raised:
                    self.engine.submit(self.spec, self.directory)
                self.assertTrue(raised.exception.uncertain)
                self.assertNotIn('sensitive-log', str(raised.exception))
                self.assertEqual(run.call_count, 1)

    def test_explicit_platform_rejection_is_failed(self):
        for code in (0, 1):
            with self.subTest(code=code), patch('app.media.subprocess.run', return_value=self.result({'gen_status': 'fail', 'fail_reason': '额度不足'}, code)):
                with self.assertRaises(MediaError) as raised:
                    self.engine.submit(self.spec, self.directory)
                self.assertFalse(raised.exception.uncertain)
                self.assertEqual(str(raised.exception), '额度不足')

    def test_error_exit_with_acknowledged_id_preserves_original_task(self):
        with patch('app.media.subprocess.run', return_value=self.result({'submit_id': TASK}, 1)):
            self.assertEqual(self.engine.submit(self.spec, self.directory)['submit_id'], TASK)

    def test_process_creation_failure_is_not_ambiguous(self):
        with patch('app.media.subprocess.run', side_effect=FileNotFoundError()):
            with self.assertRaises(MediaError) as raised:
                self.engine.submit(self.spec, self.directory)
        self.assertFalse(raised.exception.uncertain)

    def test_unknown_nonzero_error_is_conservatively_uncertain(self):
        with patch('app.media.subprocess.run', return_value=self.result({'error': 'connection reset'}, 1)):
            with self.assertRaises(MediaError) as raised:
                self.engine.submit(self.spec, self.directory)
        self.assertTrue(raised.exception.uncertain)

    def test_queries_only_query_original_task(self):
        with patch('app.media.subprocess.run', return_value=self.result({'gen_status': 'processing', 'queue_info': {'queue_status': 'Queueing'}})) as run:
            result = self.engine.query(TASK, self.directory, 'image')
        self.assertEqual(result['status'], 'waiting')
        self.assertIn('排队', result['message'])
        self.assertEqual(run.call_args.args[0][1:], ['query_result', '--submit_id=' + TASK])

    def test_query_unknown_state_is_not_fabricated_as_waiting(self):
        with patch('app.media.subprocess.run', return_value=self.result({'unexpected_status': 'oops'})):
            with self.assertRaises(MediaError) as raised:
                self.engine.query(TASK, self.directory, 'video')
        self.assertFalse(raised.exception.uncertain)

    def test_query_failure_contains_specific_platform_reason(self):
        with patch('app.media.subprocess.run', return_value=self.result({'gen_status': 'fail', 'fail_reason': '参考图片未通过审核'})):
            result = self.engine.query(TASK, self.directory, 'image')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['message'], '参考图片未通过审核')

    def test_download_scans_only_valid_local_allowlisted_media(self):
        def run(args, **kwargs):
            if any(arg.startswith('--download_dir=') for arg in args):
                (self.directory / 'good.png').write_bytes(PNG)
                (self.directory / 'bad.png').write_text('not an image')
                (self.directory / 'config.json').write_text('{"secret":"hidden"}')
                (self.directory / 'preview.png').write_bytes(PNG)
            return self.result({'gen_status': 'success', 'download_path': '../outside.png'})
        with patch('app.media.subprocess.run', side_effect=run) as calls:
            result = self.engine.query(TASK, self.directory, 'image')
        self.assertEqual(result['status'], 'done')
        self.assertEqual(result['files'], [str(self.directory / 'good.png')])
        self.assertEqual(calls.call_count, 2)
        self.assertTrue(all(call.args[0][1] == 'query_result' for call in calls.call_args_list))

    def test_existing_download_is_reused_without_overwriting(self):
        self.directory.mkdir(parents=True)
        (self.directory / 'completed.mp4').write_bytes(MP4)
        with patch('app.media.subprocess.run', return_value=self.result({'gen_status': 'success'})) as run:
            result = self.engine.query(TASK, self.directory, 'video')
        self.assertEqual(result['status'], 'done')
        self.assertEqual(run.call_count, 1)

    def test_empty_download_error_never_generates_again(self):
        with patch('app.media.subprocess.run', return_value=self.result({'gen_status': 'success'})) as run:
            with self.assertRaises(MediaError) as raised:
                self.engine.query(TASK, self.directory, 'image')
        self.assertIn('不会重新生成', str(raised.exception))
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.args[0][1] == 'query_result' for call in run.call_args_list))

    def test_paths_outside_project_and_false_image_content_rejected(self):
        bad = self.root / 'invalid.png'
        bad.write_text('not an image')
        for reference in (self.root.parent / 'outside.png', bad):
            with self.subTest(reference=reference), patch('app.media.subprocess.run') as run:
                with self.assertRaises(MediaError):
                    self.engine.submit(dict(self.spec, references=[str(reference)]), self.directory)
                run.assert_not_called()
        with self.assertRaises(MediaError):
            self.engine.query(TASK, self.root.parent, 'image')

    def test_query_identifier_validated_before_process(self):
        with patch('app.media.subprocess.run') as run:
            for task in ('--help', '../filename', '', 123):
                with self.assertRaises(MediaError):
                    self.engine.query(task, self.directory, 'image')
        run.assert_not_called()

    def test_ffmpeg_does_not_reuse_external_path_environment(self):
        with patch.dict(os.environ, {'IMAGEIO_FFMPEG_EXE': str(self.root.parent / 'old-project/ffmpeg.exe')}):
            self.assertFalse(self.engine.ffmpeg_available())
        executable = self.root / '.tools' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        executable.write_bytes(b'placeholder')
        self.assertEqual(self.engine._ffmpeg(), executable)

    def test_export_normalizes_and_removes_audio_without_video_looping(self):
        source = self.root / 'source.mp4'
        source.write_bytes(MP4)
        calls = []
        def fake(args, timeout=300):
            calls.append(args)
            Path(args[-1]).write_bytes(MP4)
            return subprocess.CompletedProcess(args, 0, '', '')
        target = self.root / 'exports/final.mp4'
        with patch.object(self.engine, '_ffmpeg', return_value=Path('ffmpeg')), patch.object(self.engine, '_ffmpeg_run', side_effect=fake), patch.object(self.engine, '_duration', return_value=5):
            self.engine.export([{'path': source, 'duration': 5}], target, '9:16', False)
        self.assertTrue(target.is_file())
        self.assertTrue(all('-an' in args for args in calls))
        self.assertTrue(all('-loop' not in args and '-stream_loop' not in args for args in calls))
        self.assertIn('scale=720:1280', calls[0][calls[0].index('-vf') + 1])
        self.assertEqual(source.read_bytes(), MP4)

    def test_short_video_refused_no_freeze_or_loop_to_fill(self):
        source = self.root / 'source.mp4'
        source.write_bytes(MP4)
        def fake(args, timeout=300):
            Path(args[-1]).write_bytes(MP4)
            return subprocess.CompletedProcess(args, 0, '', '')
        target = self.root / 'exports/short.mp4'
        with patch.object(self.engine, '_ffmpeg', return_value=Path('ffmpeg')), patch.object(self.engine, '_ffmpeg_run', side_effect=fake), patch.object(self.engine, '_duration', return_value=2):
            with self.assertRaises(MediaError) as raised:
                self.engine.export([{'path': source, 'duration': 5}], target, '9:16', False)
        self.assertIn('不会循环', str(raised.exception))
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.iterdir()), [])

    def test_draft_is_burned_in_labelled_not_disguised_as_motion(self):
        def fake(args, timeout=300):
            Path(args[-1]).write_bytes(MP4)
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(self.engine, '_ffmpeg', return_value=Path('ffmpeg')), patch.object(self.engine, '_ffmpeg_run', side_effect=fake) as run, patch.object(self.engine, '_duration', return_value=5), patch.object(self.engine, '_draft_filter', return_value=",drawtext=text='STATIC STORYBOARD DRAFT'"):
            self.engine.export([{'path': self.reference, 'duration': 5}], self.root / 'exports/draft.mp4', '16:9', True)
        args = run.call_args_list[0].args[0]
        self.assertIn('-loop', args)
        self.assertIn('STATIC STORYBOARD DRAFT', args[args.index('-vf') + 1])

    def test_export_never_overwrites_existing_version(self):
        target = self.root / 'final.mp4'
        target.write_bytes(MP4)
        with patch.object(self.engine, '_ffmpeg_run') as run:
            with self.assertRaises(MediaError):
                self.engine.export([{'path': self.reference, 'duration': 5}], target, '9:16', True)
        run.assert_not_called()
        self.assertEqual(target.read_bytes(), MP4)

    def test_strict_json_rejects_nested_duplicate_keys(self):
        with self.assertRaises(ValueError):
            _parse_json('{"x":{"secret":1,"secret":2}}')


@unittest.skipUnless(os.environ.get('IDEASTUDIO_TEST_FFMPEG'), 'Optional offline FFmpeg integration: set IDEASTUDIO_TEST_FFMPEG')
class RealFFmpegTests(unittest.TestCase):
    """Only synthetic local fixtures; never reads a user's generated media."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='idea-ffmpeg-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.engine = MediaEngine(self.root)
        executable = Path(os.environ['IDEASTUDIO_TEST_FFMPEG']).resolve()
        self.assertTrue(executable.is_file())
        patched = patch.object(self.engine, '_ffmpeg', return_value=executable)
        patched.start()
        self.addCleanup(patched.stop)

    def command(self, args):
        result = self.engine._ffmpeg_run(['-loglevel', 'error', '-y', *args])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_draft_label_and_silent_multiclip_export(self):
        image = self.root / 'blue.png'
        video = self.root / 'with-audio.mp4'
        self.command(['-f', 'lavfi', '-i', 'color=c=blue:s=160x120', '-frames:v', '1', str(image)])
        self.command(['-f', 'lavfi', '-i', 'color=c=red:s=160x120:r=24', '-f', 'lavfi', '-i', 'sine=frequency=440',
                      '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
        draft = self.engine.export([{'path': image, 'duration': 5}], self.root / 'exports/static-draft.mp4', '9:16', True)
        final = self.engine.export([{'path': video, 'duration': 5}, {'path': video, 'duration': 5}], self.root / 'exports/final.mp4', '9:16', False)
        self.assertAlmostEqual(self.engine._duration(draft), 5, delta=.06)
        self.assertAlmostEqual(self.engine._duration(final), 10, delta=.12)
        metadata = self.engine._ffmpeg_run(['-i', str(final)]).stderr
        self.assertIn('720x1280', metadata)
        self.assertNotIn('Audio:', metadata)

    def test_audio_cannot_mask_a_short_video_track(self):
        video = self.root / 'short-video-long-audio.mp4'
        self.command(['-f', 'lavfi', '-i', 'color=c=green:s=160x120:r=24:d=1', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=5',
                      '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(video)])
        # Container duration is 5s, but the actual video track is only 1s.
        self.assertGreaterEqual(self.engine._duration(video), 5)
        final = self.root / 'exports/not-a-valid-film.mp4'
        with self.assertRaises(MediaError) as raised:
            self.engine.export([{'path': video, 'duration': 5}], final, '9:16', False)
        self.assertIn('不足计划', str(raised.exception))
        self.assertFalse(final.exists())


if __name__ == '__main__':
    unittest.main()
