"""Independent Dreamina adapter and local silent film export.

The durable job ledger, approval gates and duplicate-request protection belong to
the calling server. This adapter NEVER retries a generation command. Portions of
the submit/query flow derive from novel-storyboard (MIT, Copyright 2026 Howie).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile


IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.webp'})
VIDEO_EXTENSIONS = frozenset({'.mp4', '.mov', '.webm'})
RATIOS = {'9:16': (720, 1280), '16:9': (1280, 720), '1:1': (720, 720)}
IMAGE_MODELS = frozenset({'4.0', '4.1', '4.5', '4.6', '4.7', '5.0', '5.0Pro'})
VIDEO_MODELS = frozenset({'seedance2.0fast_vip', 'seedance2.0_vip', 'seedance1.0fast'})
SUBMIT_ID = re.compile(r'[A-Za-z0-9_-]{8,128}\Z')
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class MediaError(RuntimeError):
    """uncertain=True means a paid submission MAY have reached the platform."""

    def __init__(self, message: str, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-finite JSON number')


def _parse_json(raw: str) -> dict:
    # Do not salvage a JSON-looking fragment from logs: its provenance is unclear.
    value = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
    if not isinstance(value, dict):
        raise ValueError('expected a JSON object')
    return value


def _credits(data: dict) -> dict:
    value = data.get('credit_count')
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return {'credits': value}
    return {}


def _reason(data: dict, fallback: str) -> str:
    value = data.get('fail_reason')
    # Never echo complete stdout/stderr; it can contain OAuth material or URLs.
    if not isinstance(value, str) or not value.strip():
        return fallback
    value = re.sub(r'(?i)(bearer\s+|(?:token|secret|api[_-]?key|password)\s*[:=]\s*)\S+', r'\1[已隐藏]', value)
    return value.strip()[:500]


def _media_signature(path: Path, kind: str) -> bool:
    try:
        with path.open('rb') as stream:
            head = stream.read(32)
    except OSError:
        return False
    suffix = path.suffix.lower()
    if kind == 'image':
        return ((suffix == '.png' and head.startswith(b'\x89PNG\r\n\x1a\n'))
                or (suffix in ('.jpg', '.jpeg') and head.startswith(b'\xff\xd8\xff'))
                or (suffix == '.webp' and head.startswith(b'RIFF') and head[8:12] == b'WEBP'))
    return ((suffix in ('.mp4', '.mov') and head[4:8] == b'ftyp')
            or (suffix == '.webm' and head.startswith(b'\x1aE\xdf\xa3')))


class MediaEngine:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.cli = self.root / '.tools' / ('dreamina.exe' if os.name == 'nt' else 'dreamina')
        self.home = self.root / '.local' / 'dreamina-home'

    def available(self) -> bool:
        return self.cli.is_file() and not self.cli.is_symlink()

    def _ffmpeg(self) -> Path | None:
        candidates = [self.root / '.tools' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')]
        if os.name == 'nt':
            candidates += sorted((self.root / '.local/venv/Lib/site-packages/imageio_ffmpeg/binaries').glob('ffmpeg-*.exe'))
        else:
            candidates += sorted((self.root / '.local/venv/lib').glob('python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*'))
        return next((p for p in candidates if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(self.root)), None)

    def ffmpeg_available(self) -> bool:
        return self._ffmpeg() is not None

    def cli_environment(self) -> dict[str, str]:
        """Process-local environment; never reads/copies another project's auth.

        Dreamina's Go CLI resolves its .dreamina_cli folder via the OS home
        directory (USERPROFILE on Windows). Both home conventions and standard
        config/cache folders are isolated for compatibility across CLI versions.
        Login launchers MUST use this same environment.
        """
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(('DREAMINA_', 'JIMENG_'))}
        home = self._directory(self.home)
        locations = {
            'HOME': home, 'USERPROFILE': home,
            'APPDATA': home / 'AppData/Roaming', 'LOCALAPPDATA': home / 'AppData/Local',
            'XDG_CONFIG_HOME': home / '.config', 'XDG_CACHE_HOME': home / '.cache',
            'XDG_DATA_HOME': home / '.local/share',
        }
        for name, location in locations.items():
            self._directory(location)
            env[name] = str(location)
        if home.drive:
            env['HOMEDRIVE'] = home.drive
            env['HOMEPATH'] = str(home)[len(home.drive):]
        return env

    def _inside(self, value: Path) -> Path:
        path = Path(value)
        resolved = path.resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise MediaError('媒体路径不属于当前项目')
        # Reject links even when they happen to point to another allowed folder.
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise MediaError('媒体路径不能包含符号链接或目录联接')
        return resolved

    def _directory(self, directory: Path) -> Path:
        directory = self._inside(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _reference(self, value, kind='image') -> Path:
        if not isinstance(value, (str, Path)):
            raise MediaError('参考素材路径无效')
        path = self._inside(Path(value))
        if not path.is_file() or not _media_signature(path, kind):
            raise MediaError('参考素材不存在或文件格式与内容不匹配')
        return path

    def _run_cli(self, args: list[str], paid: bool = False) -> dict:
        if not self.available():
            raise MediaError('尚未安装本项目的即梦工具，请先运行安装程序')
        try:
            result = subprocess.run([str(self.cli), *args], capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=120,
                                    env=self.cli_environment(), cwd=str(self.root),
                                    creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as exc:
            raise MediaError('即梦响应超时；未自动重试。请核对原任务记录。' if paid else '查询超时，可稍后查询原任务。', uncertain=paid) from exc
        except OSError as exc:
            # Process creation failed: there was no remote submission.
            raise MediaError('无法启动本项目的即梦工具，请检查安装和文件权限') from exc
        try:
            data = _parse_json(result.stdout)
        except (ValueError, TypeError) as exc:
            raise MediaError('即梦未返回可验证的 JSON 结果；未重新提交生成请求。', uncertain=paid) from exc
        if result.returncode:
            # Even an error exit may contain an acknowledged paid task ID.
            if paid and isinstance(data.get('submit_id'), str) and SUBMIT_ID.fullmatch(data['submit_id']):
                return data
            rejected = data.get('gen_status') == 'fail'
            raise MediaError(_reason(data, '即梦返回错误，请检查独立登录状态与平台提示。'), uncertain=paid and not rejected)
        return data

    def submit(self, spec: dict, directory: Path) -> dict:
        if not isinstance(spec, dict):
            raise MediaError('生成参数无效')
        kind, prompt, ratio = spec.get('kind'), spec.get('prompt'), spec.get('ratio')
        if kind not in ('image', 'video') or not isinstance(prompt, str) or not 2 <= len(prompt.strip()) <= 5000:
            raise MediaError('请选择正确的生成类型，并使用 2 到 5000 字的提示词')
        if ratio not in RATIOS:
            raise MediaError('目前支持竖屏、横屏和方形画面')
        values = spec.get('references', [])
        if not isinstance(values, list) or len(values) > 10:
            raise MediaError('参考素材数量不符合当前模式')
        references = [self._reference(value) for value in values]
        if len(set(references)) != len(references):
            raise MediaError('请勿重复使用同一张参考图')
        self._directory(directory)
        if kind == 'image':
            model = spec.get('model', '5.0')
            if model not in IMAGE_MODELS:
                raise MediaError('不支持的即梦图片模型')
            args = ['image2image' if references else 'text2image', '--prompt=' + prompt.strip(),
                    '--ratio=' + ratio, '--model_version=' + model, '--resolution_type=2k',
                    '--generate_num=1', '--poll=0']
            # Cobra StringSlice accepts repeated flags. Avoid comma splitting a
            # filename by rejecting commas until the CLI offers an opaque list.
            for reference in references:
                if ',' in str(reference):
                    raise MediaError('参考图路径中不能包含逗号，请重命名后重试')
                args += ['--images=' + str(reference)]
        else:
            model = spec.get('model', 'seedance2.0fast_vip')
            duration = spec.get('duration')
            if model not in VIDEO_MODELS:
                raise MediaError('不支持的视频模型；实际权限以即梦账号为准')
            if type(duration) is not int or duration not in (5, 10):
                raise MediaError('单镜头时长必须为 5 秒或 10 秒')
            if len(references) != 1:
                raise MediaError('图生视频必须使用且仅使用一张已确认的分镜图')
            args = ['image2video', '--image=' + str(references[0]), '--prompt=' + prompt.strip(),
                    '--model_version=' + model, '--duration=' + str(duration), '--ratio=' + ratio,
                    '--video_resolution=720p', '--poll=0']
        data = self._run_cli(args, paid=True)
        submit_id = data.get('submit_id')
        if isinstance(submit_id, str) and SUBMIT_ID.fullmatch(submit_id):
            return {'submit_id': submit_id, **_credits(data)}
        if data.get('gen_status') == 'fail':
            raise MediaError(_reason(data, '即梦明确拒绝了本次生成请求'))
        raise MediaError('提交结果没有可核验的任务编号；请检查即梦任务记录，系统不会自动重试。', uncertain=True)

    def _downloaded(self, directory: Path, kind: str) -> list[str]:
        allowed = IMAGE_EXTENSIONS if kind == 'image' else VIDEO_EXTENSIONS
        files = []
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in allowed:
                continue
            if path.name.startswith(('preview.', 'partial.')) or path.resolve().parent != directory:
                continue
            if path.stat().st_size > 0 and _media_signature(path, kind):
                files.append(str(path.resolve()))
        return files

    def query(self, submit_id: str, directory: Path, kind: str) -> dict:
        if not isinstance(submit_id, str) or not SUBMIT_ID.fullmatch(submit_id):
            raise MediaError('即梦任务编号格式无效')
        if kind not in ('image', 'video'):
            raise MediaError('任务类型无效')
        directory = self._directory(directory)
        result = self._run_cli(['query_result', '--submit_id=' + submit_id])
        state = result.get('gen_status')
        credits = _credits(result)
        if state == 'fail':
            return {'status': 'failed', 'message': _reason(result, '即梦返回生成失败'), 'files': [], **credits}
        if state == 'success':
            files = self._downloaded(directory, kind)
            if not files:
                downloaded = self._run_cli(['query_result', '--submit_id=' + submit_id, '--download_dir=' + str(directory)])
                if downloaded.get('gen_status') not in (None, 'success'):
                    raise MediaError('即梦生成已完成，但下载返回了不同状态；未重新生成。')
                files = self._downloaded(directory, kind)
            if not files:
                raise MediaError('即梦生成已完成，但本地尚无有效媒体文件；稍后只重试查询和下载，不会重新生成。')
            return {'status': 'done', 'message': '已完成并下载到本项目', 'files': files, **credits}
        # Do not turn malformed/unknown statuses into a reassuring fake queue.
        if state in ('pending', 'queued', 'queueing', 'waiting', 'processing', 'running', 'generating', 'in_progress', 'doing'):
            queue = result.get('queue_info')
            queueing = isinstance(queue, dict) and queue.get('queue_status') == 'Queueing'
            return {'status': 'waiting', 'message': '即梦返回排队中' if queueing or state in ('queued', 'queueing') else '即梦任务尚未完成', 'files': [], **credits}
        raise MediaError('即梦返回了无法识别的任务状态；请稍后查询原任务，不会重新提交。')

    def _ffmpeg_run(self, args: list[str], timeout: int = 300):
        ffmpeg = self._ffmpeg()
        if not ffmpeg:
            raise MediaError('尚未安装本项目的视频合成工具，请先运行安装程序')
        try:
            result = subprocess.run([str(ffmpeg), '-hide_banner', '-nostdin', *args], capture_output=True,
                                    encoding='utf-8', errors='replace', timeout=timeout,
                                    creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MediaError('本地视频处理失败或超时，已保留原始素材') from exc
        return result

    def _duration(self, path: Path) -> float:
        result = self._ffmpeg_run(['-i', str(path)], timeout=60)
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', result.stderr or '')
        if not match or not re.search(r'Stream[^\r\n]*Video:', result.stderr or ''):
            raise MediaError('无法验证视频时长；不会把无法确认的文件拼接为成片')
        hours, minutes, seconds = map(float, match.groups())
        return hours * 3600 + minutes * 60 + seconds

    @staticmethod
    def _draft_filter() -> str:
        fonts = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arial.ttf',
                 Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
                 Path('/System/Library/Fonts/Supplemental/Arial.ttf')]
        font = next((p for p in fonts if p.is_file()), None)
        if font is None:
            raise MediaError('找不到草稿标记字体；请安装 Arial 或 DejaVu Sans 后再导出草稿')
        escaped = str(font.resolve()).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        return (",drawbox=x=0:y=0:w=iw:h=72:color=black@0.78:t=fill,"
                f"drawtext=fontfile='{escaped}':text='STATIC STORYBOARD DRAFT':"
                'fontcolor=white:fontsize=26:x=(w-text_w)/2:y=24')

    def export(self, clips: list[dict], destination: Path, ratio: str, draft: bool) -> Path:
        if not isinstance(clips, list) or not 1 <= len(clips) <= 60:
            raise MediaError('请选择 1 至 60 个已完成镜头')
        if ratio not in RATIOS or type(draft) is not bool:
            raise MediaError('导出画幅或类型无效')
        destination = self._inside(destination)
        if destination.suffix.lower() != '.mp4' or destination.exists():
            raise MediaError('导出目标必须为新的 MP4 文件，不会覆盖已有版本')
        normalized = []
        for clip in clips:
            if not isinstance(clip, dict) or type(clip.get('duration')) is not int or clip['duration'] not in (5, 10):
                raise MediaError('每个镜头必须指定 5 秒或 10 秒时长')
            path = self._reference(clip.get('path'), 'image' if draft else 'video')
            normalized.append((path, clip['duration']))
        if not self.ffmpeg_available():
            raise MediaError('尚未安装本项目的视频合成工具，请先运行安装程序')
        self._directory(destination.parent)
        width, height = RATIOS[ratio]
        filters = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                   f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1')
        if draft:
            filters += self._draft_filter()
        # All temporary files are generated under this unique export directory;
        # original media and earlier exports are never altered or removed.
        with tempfile.TemporaryDirectory(prefix='.render-', dir=destination.parent) as work:
            work = Path(work)
            for index, (source, duration) in enumerate(normalized):
                output = work / f'{index:03d}.mp4'
                args = ['-loglevel', 'error', '-y']
                if draft:
                    args += ['-loop', '1', '-framerate', '24']
                args += ['-i', str(source), '-t', str(duration), '-map', '0:v:0', '-an',
                         '-vf', filters, '-r', '24', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
                         '-pix_fmt', 'yuv420p', '-threads', '2', '-movflags', '+faststart', str(output)]
                result = self._ffmpeg_run(args)
                if result.returncode or not output.is_file() or not output.stat().st_size:
                    raise MediaError('本地镜头转换失败，原素材保持不变；请检查文件是否损坏或工具是否完整')
                actual = self._duration(output)
                if actual < duration - 0.06:
                    raise MediaError(f'第 {index + 1} 镜头只有 {actual:.2f} 秒，不足计划的 {duration} 秒；不会循环或冻结画面补时长')
                if abs(actual - duration) > 0.10:
                    raise MediaError(f'第 {index + 1} 镜头导出时长验证失败')
            manifest = work / 'concat.txt'
            manifest.write_text(''.join(f"file '{index:03d}.mp4'\n" for index in range(len(normalized))), encoding='utf-8')
            combined = work / 'combined.mp4'
            result = self._ffmpeg_run(['-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
                                      '-i', str(manifest), '-map', '0:v:0', '-c:v', 'copy', '-an',
                                      '-movflags', '+faststart', str(combined)])
            if result.returncode or not combined.is_file() or not combined.stat().st_size:
                raise MediaError('本地拼接失败，已完成的镜头保持不变')
            if abs(self._duration(combined) - sum(duration for _, duration in normalized)) > 0.12:
                raise MediaError('拼接后的总时长验证失败，未输出不完整成片')
            if destination.exists():
                raise MediaError('目标版本已存在，不会覆盖')
            os.replace(combined, destination)
        return destination
