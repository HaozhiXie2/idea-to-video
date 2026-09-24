"""Guided creation domain and durable single-dispatch paid task ledger."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from .media import MediaEngine
from .planner import TextService, text
from .store import LOCK, Store, identifier, public_project, revision, uid

ACTIVE = {'queued', 'submitting', 'waiting', 'downloading'}


def find(items, key):
    return next((item for item in items if item['id'] == key), None)


def selected(entity, kind):
    return find(entity['versions'], entity.get('selected_' + kind))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def editable(p):
    if p.get('text_status') == 'analyzing':
        raise ValueError('文字分析正在进行，请等待返回后再修改')
    if any(j['status'] in ACTIVE | {'uncertain'} for j in p['jobs']):
        raise ValueError('已有进行中或结果未确认的任务。请先核对恢复原任务；尚未提交的任务可暂停并取消后再修改。')


def invalidate(p, shot_ids, images=True):
    for s in p['shots']:
        if s['id'] in shot_ids:
            if images:
                s['image_stale'] = True
            s['video_stale'] = True
    if shot_ids and images:
        p['approvals']['storyboard'] = False
    if p.get('trial', {}).get('shot_id') in shot_ids:
        p['trial'].update(approved=False, version_id=None)
    for e in p['exports']:
        e['stale'] = True
    p['_quotes'] = {}


class Studio:
    def __init__(self, root, media=None, planner=None):
        self.root = Path(root).resolve()
        self.store = Store(root)
        self.media = media or MediaEngine(self.root)
        self.planner = planner or TextService(self.root)
        self.worker_lock = threading.Lock()
        self.stop_event = threading.Event()

    def status(self):
        return {'app': 'idea-to-video', 'version': 1, 'cli_available': self.media.available(),
                'ffmpeg_available': self.media.ffmpeg_available(), 'text_configured': self.planner.configured()}

    def create(self, data):
        source = text(data.get('source'), '输入文本', 60000)
        template = data.get('template', 'story')
        duration, ratio = data.get('duration', 30), data.get('ratio', '9:16')
        if template not in ('story', 'product', 'knowledge', 'memory'):
            raise ValueError('请选择一种创作模板')
        if type(duration) is not int or duration not in (15, 30, 60, 90) or ratio not in ('9:16', '16:9', '1:1'):
            raise ValueError('不支持的时长或画幅')
        p = dict(id=uid(), title=text(data.get('title') or source[:24], '作品名称', 120), source=source,
                 template=template, duration=duration, ratio=ratio, revision=1, phase='idea',
                 created=time.time(), updated=time.time(), answers={}, questions=[], plan=None,
                 approvals=dict(plan=False, assets=False, storyboard=False), assets=[], shots=[], jobs=[],
                 paused=False, history=[], exports=[], text_status='idle', _quotes={}, _batches={}, _proposals={},
                 planning_stage=None, questions_stage=None, style_asset_id=None, batch_limit=3,
                 trial=dict(shot_id=None, version_id=None, approved=False), _answered=[])
        self.store.save(p)
        return public_project(p)

    def analyze(self, pid, data):
        with LOCK:
            p = self.store.read(pid)
            revision(p, data.get('expected_revision'))
            editable(p)
            stage = data.get('stage', 'plan')
            if stage not in ('plan', 'assets', 'storyboard'):
                raise ValueError('未知创作阶段')
            if stage == 'plan' and p['approvals']['plan']:
                raise ValueError('方案已确认，请使用局部修改或恢复旧版本，避免覆盖已制作内容')
            if stage == 'assets' and (not p['approvals']['plan'] or any(a['versions'] for a in p['assets'])):
                raise ValueError('请先确认方案；已有素材时请局部修改或新建变体，不要覆盖全部形象')
            if stage == 'storyboard' and (not p['approvals']['assets'] or any(s['versions'] for s in p['shots'])):
                raise ValueError('请先确认形象；已有分镜素材时请局部修改，不要覆盖全部镜头')
            if p['questions'] and p.get('questions_stage') != stage:
                raise ValueError('请先回答当前阶段的问题')
            if not self.planner.configured():
                raise ValueError('尚未配置文字分析服务。请先在设置中填写服务地址、模型和密钥。')
            answers = data.get('answers', {})
            if not isinstance(answers, dict):
                raise ValueError('补充信息格式错误')
            allowed = {q['id'] for q in p['questions']}
            if any(key not in allowed for key in answers):
                raise ValueError('问题已更新，请刷新后回答')
            for key, value in answers.items():
                p['answers'][key] = text(value, '回答', 10000)
            if any(not p['answers'].get(key) for key in allowed):
                raise ValueError('请集中回答本轮问题；暂未确定也请说明缺少什么')
            op = uid()
            for q in p['questions']:
                p['_answered'] = [a for a in p['_answered'] if a['id'] != q['id']]
                p['_answered'].append(dict(id=q['id'], stage=stage, question=q['question'], answer=p['answers'][q['id']]))
            p.update(text_status='analyzing', text_error='', _text_op=op)
            self.store.save(p)
            request_p = copy.deepcopy(p)
        try:
            result = self.planner.analyze(request_p, stage)
            with LOCK:
                p = self.store.read(pid)
                if p.get('_text_op') != op:
                    raise ValueError('分析记录已变更，请刷新查看')
                self.store.snapshot(p, '分析前的内容')
                if result['questions']:
                    for i, q in enumerate(result['questions']):
                        q['id'] = stage + '_' + op[:12] + '_' + str(i)
                    result['questions_stage'] = stage
                else:
                    result['questions_stage'] = None
                    result['planning_stage'] = stage
                p.update(result)
                p.update(text_status='idle', text_error='', phase='questions' if p['questions'] else stage)
                p['_quotes'] = {}
                self.store.save(p, bump=True)
                return public_project(p)
        except Exception as exc:
            with LOCK:
                p = self.store.read(pid)
                p.update(text_status='idle', text_error=str(exc))
                self.store.save(p)
            raise

    def approve(self, p, data):
        revision(p, data.get('expected_revision'))
        editable(p)
        stage = data.get('stage')
        if stage == 'plan':
            if not p['plan'] or p['questions']:
                raise ValueError('请先完成创作问答，检查制作方案')
            p['approvals']['plan'] = True
            p['phase'] = 'assets'
        elif stage == 'assets':
            if not p['approvals']['plan'] or p['planning_stage'] not in ('assets', 'storyboard') or p['questions']:
                raise ValueError('请先确认制作方案')
            if any(not a['approved'] or not selected(a, 'image') for a in p['assets']):
                raise ValueError('请为每个关键形象选择并确认一张参考图')
            p['approvals']['assets'] = True
            p['phase'] = 'storyboard'
        elif stage == 'storyboard':
            if not p['approvals']['assets'] or not p['shots'] or any(s['image_stale'] or not selected(s, 'image') for s in p['shots']):
                raise ValueError('请先确认关键形象，并完成所有分镜图片')
            p['approvals']['storyboard'] = True
            p['phase'] = 'video'
        elif stage == 'motion':
            if not p['approvals']['storyboard']:
                raise ValueError('请先确认当前分镜草稿')
            shot = find(p['shots'], p['trial']['shot_id'])
            version = selected(shot, 'video') if shot else None
            if not version or shot['video_stale'] or shot['image_stale']:
                raise ValueError('请先完成正式试镜并观看当前视频版本')
            p['trial'].update(approved=True, version_id=version['id'])
            p['phase'] = 'video'
        else:
            raise ValueError('未知确认步骤')
        return p

    def upload(self, p, data):
        revision(p, data.get('expected_revision'))
        editable(p)
        if not p['approvals']['plan']:
            raise ValueError('请先确认制作方案')
        asset = find(p['assets'], data.get('asset_id'))
        if not asset:
            raise ValueError('关键形象不存在')
        try:
            raw = base64.b64decode(data.get('data', ''), validate=True)
        except (ValueError, TypeError):
            raise ValueError('图片编码不正确') from None
        ext = '.png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else '.jpg' if raw.startswith(b'\xff\xd8\xff') else '.webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None
        if not ext or not 32 <= len(raw) <= 8 * 1024 * 1024:
            raise ValueError('请上传 8MB 以内的 PNG、JPEG 或 WebP 图片')
        self.store.snapshot(p, '上传参考图前')
        key = uid()
        rel = 'uploads/' + key + ext
        dest = self.store.folder(p['id']) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        asset['versions'].append(dict(id=key, kind='image', url=f'/media/{p["id"]}/{rel}',
                                      created=time.time(), prompt='', label='上传的参考图'))
        # Upload is a candidate, not an implicit identity approval.
        return p

    def select(self, p, data):
        revision(p, data.get('expected_revision'))
        editable(p)
        entity_type, kind = data.get('entity'), data.get('kind')
        if entity_type not in ('asset', 'shot') or kind not in ('image', 'video') or (entity_type == 'asset' and kind != 'image'):
            raise ValueError('无效版本类型')
        entity = find(p['assets' if entity_type == 'asset' else 'shots'], data.get('entity_id'))
        version = find(entity['versions'], data.get('version_id')) if entity else None
        if not version or version['kind'] != kind:
            raise ValueError('所选版本不属于这个内容')
        self.version_path(p, version)
        if entity_type == 'shot' and kind == 'video' and entity['image_stale']:
            raise ValueError('请先确定当前分镜图片，再选择其视频版本')
        self.store.snapshot(p, '切换素材版本前')
        changed = entity.get('selected_' + kind) != version['id']
        entity['selected_' + kind] = version['id']
        if entity_type == 'asset':
            entity['approved'] = True
            if changed:
                p['approvals']['assets'] = False
                invalidate(p, [s['id'] for s in p['shots'] if entity['id'] in s['asset_ids'] or p.get('style_asset_id') == entity['id']])
        else:
            entity[kind + '_stale'] = False
            if kind == 'image' and changed:
                invalidate(p, [entity['id']], images=False)
                p['approvals']['storyboard'] = False
            if kind == 'video' and changed and p['trial']['shot_id'] == entity['id']:
                p['trial'].update(approved=False, version_id=None)
            for exp in p['exports']:
                exp['stale'] = True
        return p

    def version_path(self, p, version):
        prefix = f'/media/{p["id"]}/'
        if not version['url'].startswith(prefix):
            raise ValueError('参考图不属于当前项目')
        return self.store.file(p['id'], version['url'][len(prefix):])

    def spec(self, p, kind, entity):
        refs = []
        if kind == 'asset_image':
            prompt = entity['prompt']
            previous = selected(entity, 'image') or find(entity['versions'], entity.get('anchor_image'))
            if not previous and entity.get('source_asset_id'):
                source_asset = find(p['assets'], entity['source_asset_id'])
                previous = selected(source_asset, 'image') if source_asset else None
            if previous:
                refs.append(str(self.version_path(p, previous)))
                prompt += '\n参考图1仅作为已确认身份或产品外观参考，按上述修改保持其余特征。单幅画面，不做多视图拼贴。'
        elif kind == 'shot_image':
            prompt = entity['image_prompt']
            roles = []
            asset_refs = list(entity['asset_ids'])
            if p.get('style_asset_id') and p['style_asset_id'] not in asset_refs:
                asset_refs.append(p['style_asset_id'])
            for i, asset_id in enumerate(asset_refs, 1):
                asset = find(p['assets'], asset_id)
                version = selected(asset, 'image')
                if not asset['approved'] or not version:
                    raise ValueError('参考形象尚未确认')
                refs.append(str(self.version_path(p, version)))
                role = '仅光影、色调与视觉风格参考，不增加其中人物或物体' if asset_id == p.get('style_asset_id') and asset_id not in entity['asset_ids'] else asset['reference_role']
                roles.append(f'参考图{i}：{asset["name"]}，用途{role}；{asset["description"]}')
            prompt += '\n' + '\n'.join(roles) + '\n单幅分镜首帧，不要拼贴、字幕、水印。保持参考主体特征。'
        else:
            if entity['image_stale'] or not selected(entity, 'image'):
                raise ValueError('分镜图片需要先更新并确认')
            refs = [str(self.version_path(p, selected(entity, 'image')))]
            prompt = entity['video_prompt'] + '\n以输入图片为首帧，保持主体身份、服饰与场景。连续单镜头。不要字幕、水印。'
        if len(prompt) > 5000:
            raise ValueError('合并参考说明后的提示词超过 5000 字，请简化这个镜头或减少参考素材')
        return dict(kind='video' if kind == 'shot_video' else 'image', prompt=prompt,
                    ratio=p['ratio'], model='seedance2.0fast_vip' if kind == 'shot_video' else '5.0',
                    duration=entity.get('duration', 5), references=refs)

    def quote(self, p, data):
        kind = data.get('kind')
        if kind not in ('asset_image', 'shot_image', 'shot_video'):
            raise ValueError('生成类型不正确')
        if p.get('text_status') == 'analyzing':
            raise ValueError('请等待文字分析结束')
        if not self.media.available():
            raise ValueError('尚未安装即梦工具，请先运行安装器')
        if not p['approvals']['plan'] or (kind != 'asset_image' and not p['approvals']['assets']) or (kind == 'shot_video' and not p['approvals']['storyboard']):
            raise ValueError('请先完成当前步骤之前的确认')
        candidates = p['assets'] if kind == 'asset_image' else p['shots']
        ids = data.get('entity_ids', [])
        if not isinstance(ids, list) or not all(isinstance(k, str) for k in ids) or len(ids) != len(set(ids)):
            raise ValueError('生成目标不正确')
        if not ids:
            field = 'video' if kind == 'shot_video' else 'image'
            ids = [e['id'] for e in candidates if not selected(e, field) or e.get(field + '_stale')]
        if not ids or (kind == 'asset_image' and len(ids) != 1):
            raise ValueError('关键形象每次只生成一个候选；分镜请至少选择一个需要制作的镜头')
        if len(ids) > p['batch_limit']:
            raise ValueError(f'本批有 {len(ids)} 个任务，超过你设置的单批上限 {p["batch_limit"]}；请减少选择或调整上限')
        if kind == 'shot_video' and not self.trial_valid(p):
            if ids != [p['trial']['shot_id']]:
                raise ValueError('请先选择一个正式镜头试拍，并确认它的运动效果；试镜通过后才可批量生成其他镜头')
        specs, items = [], []
        for key in ids:
            entity = find(candidates, key)
            if not entity:
                raise ValueError('生成目标不存在')
            if any(j['entity_id'] == key and j['kind'] == kind and j['status'] in ACTIVE | {'uncertain'} for j in p['jobs']):
                raise ValueError('这个内容已有进行中或结果未确认的任务，请先恢复原任务，不能重复提交')
            specs.append(dict(entity_id=key, spec=self.spec(p, kind, entity)))
            items.append({'entity_id': key, 'label': entity.get('name', entity.get('title', key))})
        quote = dict(quote_id=uid(), count=len(items), kind=kind, items=items, cost_known=False,
                     warning=f'本次将提交 {len(items)} 个即梦任务，费用未知，可能消耗积分。参考图和提示词将发送到即梦。不会自动重试结果不明的请求。',
                     revision=p['revision'])
        p['_quotes'][quote['quote_id']] = dict(quote, _specs=specs, _created=time.time())
        self.store.save(p)
        return quote

    def generate(self, p, data):
        if data.get('confirmed') is not True:
            raise ValueError('请先确认本批任务数量与可能产生的费用')
        request_id = identifier(data.get('request_id'))
        quote_id = data.get('quote_id')
        previous = p['_batches'].get(request_id)
        if previous:
            if previous['quote_id'] != quote_id:
                raise ValueError('同一提交编号不能用于不同批次')
            return self.batch_public(p, previous)
        quote = p['_quotes'].get(quote_id)
        if not quote or quote.get('_used') or quote['revision'] != p['revision'] or time.time() - quote['_created'] > 1800:
            raise ValueError('任务确认单已过期、已使用或内容已变化，请重新查看任务数量')
        if p['paused']:
            raise ValueError('项目已暂停，请先恢复后确认生成')
        # Revalidate gates, files and duplicates under the same lock as reservation.
        for row in quote['_specs']:
            if any(j['entity_id'] == row['entity_id'] and j['kind'] == quote['kind'] and j['status'] in ACTIVE | {'uncertain'} for j in p['jobs']):
                raise ValueError('已有相同内容的任务，未重复提交')
            # A quote's references must still exist when the user consents.
            for ref in row['spec']['references']:
                resolved = Path(ref).resolve()
                if not resolved.is_relative_to(self.store.folder(p['id']).resolve()) or not resolved.is_file():
                    raise ValueError('参考文件已变化，请重新确认素材后生成')
        batch = dict(batch_id=uid(), quote_id=quote_id, job_ids=[])
        for row in quote['_specs']:
            key = uid()
            job = dict(id=key, project_id=p['id'], entity_id=row['entity_id'], kind=quote['kind'],
                       status='queued', message='已确认，等待本机依次提交', created=time.time(),
                       images=[], videos=[], _spec=row['spec'], batch_id=batch['batch_id'])
            p['jobs'].append(job)
            batch['job_ids'].append(key)
        quote['_used'] = True
        p['_batches'][request_id] = batch
        self.store.save(p)
        return self.batch_public(p, batch)

    def batch_public(self, p, batch):
        public = public_project(p)
        return dict(batch_id=batch['batch_id'], jobs=[j for j in public['jobs'] if j['id'] in batch['job_ids']], project=public)

    def pause(self, p, data):
        if type(data.get('paused')) is not bool:
            raise ValueError('暂停状态格式不正确')
        p['paused'] = data['paused']
        if data.get('cancel_queued') is True:
            for j in p['jobs']:
                if j['status'] == 'queued':
                    j.update(status='canceled', message='已取消尚未提交的任务，未调用生成接口')
        self.store.save(p)
        return public_project(p)

    def recover_job(self, p, data):
        job = find(p['jobs'], data.get('job_id'))
        if not job:
            raise ValueError('任务不存在')
        supplied = data.get('submit_id')
        if supplied and supplied == job.get('submit_id'):
            supplied = None  # Querying the already-bound ID is not a rebind.
        if supplied:
            if job['status'] != 'uncertain' or job.get('submit_id'):
                raise ValueError('只有未取得平台编号的结果不明任务可以绑定已核对的编号')
            if not isinstance(supplied, str) or not re.fullmatch(r'[a-fA-F0-9-]{32,36}', supplied):
                raise ValueError('请填写在即梦记录中亲自核对的任务编号')
            if any(j.get('submit_id') == supplied for row in self.store.list() if row['phase'] != 'damaged' for j in self.store.read(row['id'])['jobs']):
                raise ValueError('这个平台任务编号已绑定到其他记录')
            job['submit_id'] = supplied
        if not job.get('submit_id'):
            raise ValueError('未取得平台任务编号，无法安全恢复。请先在即梦核对，再填写原任务编号。不会重新生成。')
        if job['status'] not in ('done', 'failed', 'canceled'):
            job.update(status='waiting', message='恢复查询原任务，不重新生成')
        self.store.save(p)
        return public_project(p)

    @staticmethod
    def trial_valid(p):
        trial = p.get('trial', {})
        shot = find(p['shots'], trial.get('shot_id'))
        return bool(trial.get('approved') and shot and not shot['image_stale'] and not shot['video_stale']
                    and shot.get('selected_video') == trial.get('version_id'))

    def asset_variant(self, p, data):
        revision(p, data.get('expected_revision'))
        editable(p)
        original = find(p['assets'], data.get('asset_id'))
        targets = data.get('shot_ids')
        if not original or len(p['assets']) >= 24 or not isinstance(targets, list) or not targets:
            raise ValueError('请选定原形象及需要新服装/场景的镜头；最多保留24项形象（含变体）')
        if len(targets) != len(set(targets)) or any(not find(p['shots'], t) or original['id'] not in find(p['shots'], t)['asset_ids'] for t in targets):
            raise ValueError('所选镜头未使用这个形象')
        self.store.snapshot(p, '新建服装或场景变体前')
        variant = dict(id=uid(), type=original['type'], reference_role=original['reference_role'],
                       name=text(data.get('name'), '变体名称', 120), description=text(data.get('description'), '变体设定', 2000),
                       prompt=text(data.get('prompt'), '变体提示词', 4000), selected_image=None, approved=False, versions=[],
                       source_asset_id=original['id'])
        p['assets'].append(variant)
        for s in p['shots']:
            if s['id'] in targets:
                s['asset_ids'] = [variant['id'] if key == original['id'] else key for key in s['asset_ids']]
        p['approvals']['assets'] = False
        invalidate(p, targets)
        p['phase'] = 'assets'
        return p

    def propose(self, pid, data):
        with LOCK:
            p = self.store.read(pid)
            revision(p, data.get('expected_revision'))
            editable(p)
            group = 'assets' if data.get('target') == 'asset' else 'shots' if data.get('target') == 'shot' else ''
            target = find(p.get(group, []), data.get('target_id'))
            if not target:
                raise ValueError('请选择要修改的镜头或关键形象')
            instruction = text(data.get('instruction'), '修改要求', 5000)
            if not self.planner.configured():
                raise ValueError('请先配置文字分析服务')
            p['text_status'] = 'analyzing'
            self.store.save(p)
        try:
            result = self.planner.edit(p, target, instruction)
            if result.get('questions'):
                questions = result['questions'][:3]
                raise ValueError('修改前需要补充：' + '；'.join(str(q.get('question', '请补充要求')) for q in questions))
            changes = result.get('changes')
            allowed = {'description', 'prompt', 'name'} if group == 'assets' else {'description', 'camera', 'action', 'image_prompt', 'video_prompt', 'title'}
            if not isinstance(changes, dict) or not changes or any(key not in allowed for key in changes):
                raise ValueError('修改提案格式不正确，没有变更原内容')
            changes = {key: text(value, '修改内容', 4500 if 'prompt' in key else 2000) for key, value in changes.items()}
            if group == 'shots':
                if set(changes) & {'camera', 'action'} and 'video_prompt' not in changes:
                    raise ValueError('修改动作或运镜时需要同步给出完整视频提示词，本次提案未应用')
                if 'description' in changes and 'image_prompt' not in changes:
                    raise ValueError('修改画面时需要同步给出完整生图提示词，本次提案未应用')
            if group == 'assets' and 'description' in changes and 'prompt' not in changes:
                raise ValueError('修改形象时需要同步给出完整生图提示词，本次提案未应用')
            proposal = dict(proposal_id=uid(), summary=text(result.get('summary'), '修改说明', 2000), changes=changes,
                            affected_shots=[s['id'] for s in p['shots'] if target['id'] in s['asset_ids'] or p.get('style_asset_id') == target['id']] if group == 'assets' else [target['id']],
                            revision=p['revision'], _group=group, _target=target['id'])
            with LOCK:
                current = self.store.read(pid)
                revision(current, p['revision'])
                current['_proposals'][proposal['proposal_id']] = proposal
                self.store.save(current)
            return {k: v for k, v in proposal.items() if not k.startswith('_')}
        finally:
            with LOCK:
                current = self.store.read(pid)
                current['text_status'] = 'idle'
                self.store.save(current)

    def apply_edit(self, p, data):
        revision(p, data.get('expected_revision'))
        editable(p)
        proposal = p['_proposals'].get(data.get('proposal_id'))
        if not proposal or proposal['revision'] != p['revision']:
            raise ValueError('修改提案已过期，请重新提出要求')
        self.store.snapshot(p, '修改前：' + proposal['summary'][:80])
        entity = find(p[proposal['_group']], proposal['_target'])
        entity.update(proposal['changes'])
        if proposal['_group'] == 'assets':
            entity['approved'] = False
            entity['anchor_image'] = entity.get('selected_image')
            entity['selected_image'] = None
            p['approvals']['assets'] = False
        changes = set(proposal['changes'])
        images = proposal['_group'] == 'assets' or bool(changes - {'video_prompt', 'camera', 'action', 'title'})
        if changes - {'title', 'name'}:
            invalidate(p, proposal['affected_shots'], images=images)
        p['phase'] = 'assets' if proposal['_group'] == 'assets' else 'storyboard' if images else 'video'
        p['_proposals'] = {}
        return p

    def mutate(self, pid, action, data):
        with LOCK:
            p = self.store.read(pid)
            if action == 'quote':
                return self.quote(p, data)
            if action == 'generate':
                return self.generate(p, data)
            if action == 'pause':
                return self.pause(p, data)
            if action == 'recover':
                return self.recover_job(p, data)
            if action in ('approve', 'upload', 'select', 'apply-edit'):
                p = getattr(self, action.replace('-', '_'))(p, data)
            elif action == 'settings':
                revision(p, data.get('expected_revision'))
                limit = data.get('batch_limit')
                if type(limit) is not int or not 1 <= limit <= 18:
                    raise ValueError('单批任务上限必须为1到18')
                p['batch_limit'] = limit
                p['_quotes'] = {}
            elif action == 'trial':
                revision(p, data.get('expected_revision'))
                editable(p)
                shot = find(p['shots'], data.get('shot_id'))
                if not shot or not p['approvals']['storyboard'] or shot['image_stale']:
                    raise ValueError('请先确认分镜，再选择一镜正式试拍')
                p['trial'] = dict(shot_id=shot['id'], version_id=None, approved=False)
                p['_quotes'] = {}
            elif action == 'style-reference':
                revision(p, data.get('expected_revision'))
                editable(p)
                asset_id = data.get('asset_id')
                if asset_id is not None:
                    asset = find(p['assets'], asset_id)
                    if not asset or not asset['approved'] or not selected(asset, 'image'):
                        raise ValueError('请选择一张已经确认的形象或场景图作为风格参考')
                if p.get('style_asset_id') != asset_id:
                    self.store.snapshot(p, '更换风格参考前')
                    p['style_asset_id'] = asset_id
                    invalidate(p, [s['id'] for s in p['shots']])
            elif action == 'asset-variant':
                p = self.asset_variant(p, data)
            elif action == 'restore':
                revision(p, data.get('expected_revision'))
                editable(p)
                self.store.restore(p, data.get('version_id'))
                for exp in p['exports']:
                    exp['stale'] = True
            elif action == 'remove-shot':
                revision(p, data.get('expected_revision'))
                editable(p)
                shot = find(p['shots'], data.get('shot_id'))
                if not shot or len(p['shots']) <= 1:
                    raise ValueError('至少保留一个镜头')
                self.store.snapshot(p, '移除镜头前')
                archive = p.setdefault('media_archive', [])
                archived_ids = {v['id'] for v in archive}
                archive.extend(dict(v, entity_id=shot['id'], entity_label=shot['title'])
                               for v in shot['versions'] if v['id'] not in archived_ids)
                p['shots'].remove(shot)
                p['duration'] = sum(s['duration'] for s in p['shots'])
                p['approvals']['storyboard'] = False
                if p['trial']['shot_id'] == shot['id']:
                    p['trial'] = dict(shot_id=None, version_id=None, approved=False)
                invalidate(p, [])
            else:
                raise ValueError('未知操作')
            self.store.save(p, bump=True)
            return public_project(p)

    def startup_recover(self):
        with LOCK:
            for row in self.store.list():
                if row['phase'] == 'damaged':
                    continue
                p = self.store.read(row['id'])
                if p.get('text_status') == 'analyzing':
                    p.update(text_status='idle', text_error='上次文字请求中断，可能已经计费；未自动重试。')
                for j in p['jobs']:
                    if j['status'] == 'submitting':
                        j.update(status='waiting' if j.get('submit_id') else 'uncertain',
                                 message='恢复查询原任务' if j.get('submit_id') else '上次提交中断，结果未确认；没有自动重试')
                for e in p['exports']:
                    if e['status'] in ('queued', 'rendering'):
                        e.update(status='failed', message='上次本机合成中断，可重新导出；不会调用云端')
                self.store.save(p)

    def worker_once(self):
        if not self.worker_lock.acquire(blocking=False):
            return
        try:
            with LOCK:
                projects = [self.store.read(row['id']) for row in self.store.list() if row['phase'] != 'damaged']
                running = [(p['id'], j['id']) for p in projects for j in p['jobs'] if j['status'] in ('waiting', 'downloading')]
            for pid, jid in running:
                self.query_one(pid, jid)
            with LOCK:
                projects = [self.store.read(row['id']) for row in self.store.list() if row['phase'] != 'damaged']
                # One remote job at a time; unknown submissions stop further spending globally.
                if any(j['status'] in {'submitting', 'waiting', 'downloading', 'uncertain'} or j.get('query_error') for p in projects for j in p['jobs'] if j['status'] != 'canceled'):
                    return
                queue = [(p, j) for p in projects if not p['paused'] for j in p['jobs'] if j['status'] == 'queued']
                if not queue:
                    return
                p, j = min(queue, key=lambda pair: pair[1]['created'])
                j.update(status='submitting', message='正在提交给即梦，结果明确前不会重试')
                self.store.save(p)  # durable before the non-idempotent external request
                pid, jid, spec = p['id'], j['id'], copy.deepcopy(j['_spec'])
            try:
                dest = self.store.folder(pid) / 'jobs' / jid
                dest.mkdir(parents=True, exist_ok=True)
                result = self.media.submit(spec, dest)
                if not result.get('submit_id'):
                    raise RuntimeError('平台未返回任务编号')
                with LOCK:
                    p = self.store.read(pid)
                    j = find(p['jobs'], jid)
                    j.update(status='waiting', message='已提交，等待即梦处理', submit_id=result['submit_id'], credits=result.get('credits'))
                    self.store.save(p)
            except Exception as exc:
                with LOCK:
                    p = self.store.read(pid)
                    j = find(p['jobs'], jid)
                    uncertain = getattr(exc, 'uncertain', True)
                    j.update(status='uncertain' if uncertain else 'failed', message=str(exc)[:2000])
                    p['paused'] = True
                    self.store.save(p)
        finally:
            self.worker_lock.release()

    def query_one(self, pid, jid):
        with LOCK:
            p = self.store.read(pid)
            j = find(p['jobs'], jid)
            remote_id = j.get('submit_id')
            spec = j['_spec']
        if not remote_id:
            return
        try:
            dest = self.store.folder(pid) / 'jobs' / jid
            result = self.media.query(remote_id, dest, spec['kind'])
            with LOCK:
                p = self.store.read(pid)
                j = find(p['jobs'], jid)
                j.pop('query_error', None)
                state = result.get('status')
                if state not in ('waiting', 'done', 'failed'):
                    raise ValueError('平台返回未知状态，保留原任务继续查询')
                if state == 'done':
                    files = result.get('files') or []
                    valid = []
                    for filename in files:
                        path = Path(filename).resolve()
                        if not path.is_relative_to(dest.resolve()) or not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm', '.mov'):
                            raise ValueError('下载文件路径或格式不正确，未登记为完成')
                        valid.append(path)
                    if not valid:
                        raise ValueError('平台生成完成，但文件尚未下载成功；只重试下载')
                    entity = find(p['assets'] if j['kind'] == 'asset_image' else p['shots'], j['entity_id'])
                    if not entity:
                        raise ValueError('原内容不存在，文件仍保留在任务目录')
                    version_kind = spec['kind']
                    for path in valid:
                        rel = path.relative_to(self.store.folder(pid)).as_posix()
                        v = dict(id=uid(), kind=version_kind, url=f'/media/{pid}/{rel}', created=time.time(),
                                 prompt=spec['prompt'], job_id=jid, label='即梦生成')
                        entity['versions'].append(v)
                    first = entity['versions'][-len(valid)]
                    if j['kind'] != 'asset_image':
                        entity['selected_' + version_kind] = first['id']
                        entity[version_kind + '_stale'] = False
                        if version_kind == 'image':
                            entity['video_stale'] = True
                            p['approvals']['storyboard'] = False
                        if p['trial']['shot_id'] == entity['id']:
                            p['trial'].update(approved=False, version_id=None)
                        for exp in p['exports']:
                            exp['stale'] = True
                    j['images' if version_kind == 'image' else 'videos'] = [v.name for v in valid]
                    j['url'] = first['url']
                    p['revision'] += 1
                j.update(status=state, message=result.get('message', state), last_checked=time.time())
                if state == 'failed':
                    p['paused'] = True
                if result.get('credits') is not None:
                    j['credits'] = result['credits']
                self.store.save(p)
        except Exception as exc:
            with LOCK:
                p = self.store.read(pid)
                j = find(p['jobs'], jid)
                j.update(query_error=str(exc)[:2000], last_checked=time.time())
                self.store.save(p)

    def worker(self):
        while not self.stop_event.is_set():
            try:
                self.worker_once()
            except Exception:
                # A bad record cannot trigger retries of paid submissions.
                pass
            self.stop_event.wait(15)

    def start_export(self, pid, data):
        with LOCK:
            p = self.store.read(pid)
            revision(p, data.get('expected_revision'))
            editable(p)
            kind = data.get('kind')
            if kind not in ('draft', 'final') or not p['shots']:
                raise ValueError('请选择草稿或成片导出')
            if not self.media.ffmpeg_available():
                raise ValueError('本机合成工具尚未安装，请运行安装器')
            if kind == 'final' and (not p['approvals']['storyboard'] or not self.trial_valid(p)):
                raise ValueError('请先确认分镜草稿')
            media_kind = 'image' if kind == 'draft' else 'video'
            clips = []
            for shot in p['shots']:
                version = selected(shot, media_kind)
                if not version or shot[media_kind + '_stale']:
                    raise ValueError('请先完成所有当前版本的' + ('分镜图片' if kind == 'draft' else '视频镜头'))
                clips.append({'path': self.version_path(p, version), 'duration': shot['duration']})
            existing = next((e for e in p['exports'] if e['kind'] == kind and e['revision'] == p['revision'] and e['status'] in ('queued', 'rendering', 'done') and not e.get('stale')), None)
            if existing:
                return {'export_id': existing['id'], 'status': existing['status']}
            key = uid()
            p['exports'].append(dict(id=key, kind=kind, status='queued', revision=p['revision'], created=time.time(),
                                     message='等待本机合成：静态分镜草稿' if kind == 'draft' else '等待本机合成：无声 MP4'))
            self.store.save(p)
            threading.Thread(target=self.render_export, args=(pid, key, clips, p['ratio'], kind), daemon=True).start()
            return {'export_id': key, 'status': 'queued'}

    def render_export(self, pid, key, clips, ratio, kind):
        try:
            with LOCK:
                p = self.store.read(pid)
                find(p['exports'], key)['status'] = 'rendering'
                self.store.save(p)
            output = self.store.folder(pid) / 'exports' / key / ('static-draft.mp4' if kind == 'draft' else 'final-silent.mp4')
            output.parent.mkdir(parents=True, exist_ok=True)
            result = Path(self.media.export(clips, output, ratio, kind == 'draft')).resolve()
            if result != output.resolve() or not result.is_file():
                raise ValueError('本机合成没有生成预期的文件')
            with LOCK:
                p = self.store.read(pid)
                e = find(p['exports'], key)
                e.update(status='done', url=f'/media/{pid}/exports/{key}/{output.name}',
                         message='静态分镜草稿（不是动态成片）' if kind == 'draft' else '无声成片已完成')
                if kind == 'final' and not e.get('stale'):
                    p['phase'] = 'complete'
                self.store.save(p)
        except Exception as exc:
            with LOCK:
                p = self.store.read(pid)
                find(p['exports'], key).update(status='failed', message=str(exc)[:2000])
                self.store.save(p)

    def media_path(self, pid, relative):
        p = self.store.read(pid)
        url = f'/media/{pid}/{relative}'
        allowed = {v['url'] for entity in p['assets'] + p['shots'] for v in entity['versions']}
        allowed |= {v['url'] for v in p.get('media_archive', [])}
        allowed |= {e.get('url') for e in p['exports'] if e['status'] == 'done'}
        if url not in allowed:
            raise ValueError('素材不存在或不属于这个项目')
        return self.store.file(pid, relative)
