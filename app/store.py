"""Atomic local storage. Never share paths or credentials with another studio."""
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

LOCK = threading.RLock()


def uid():
    return uuid.uuid4().hex


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('无效的项目或内容编号')
    return value


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.projects = self.root / 'data/projects'
        self.projects.mkdir(parents=True, exist_ok=True)

    def folder(self, key):
        path = self.projects / identifier(key)
        if path.is_symlink() or path.resolve().parent != self.projects.resolve():
            raise ValueError('无效项目目录')
        return path

    def read(self, key):
        with LOCK:
            try:
                result = json.loads((self.folder(key) / 'project.json').read_text(encoding='utf-8'))
            except FileNotFoundError as exc:
                raise ValueError('项目不存在') from exc
            if (not isinstance(result, dict) or result.get('id') != key
                    or type(result.get('revision')) is not int
                    or not isinstance(result.get('approvals'), dict)
                    or any(not isinstance(result.get(k), list) for k in ('jobs', 'assets', 'shots', 'history', 'exports', 'questions'))
                    or any(not isinstance(x, dict) for k in ('jobs', 'assets', 'shots', 'history', 'exports', 'questions') for x in result[k])):
                raise ValueError('项目记录损坏，请保留文件并从备份恢复')
            return result

    def save(self, project, bump=False):
        with LOCK:
            if bump:
                project['revision'] += 1
            project['updated'] = time.time()
            atomic(self.folder(project['id']) / 'project.json', project)

    def list(self):
        result = []
        for path in self.projects.glob('*/project.json'):
            try:
                p = self.read(path.parent.name)
                result.append({k: p[k] for k in ('id', 'title', 'template', 'duration', 'ratio', 'updated', 'phase')})
            except (OSError, ValueError, KeyError, TypeError):
                # Keep damaged files untouched, isolate them from other projects.
                result.append({'id': path.parent.name, 'title': '记录无法读取（文件已保留）',
                               'phase': 'damaged', 'updated': 0, 'template': '', 'duration': 0, 'ratio': ''})
        return sorted(result, key=lambda p: p['updated'], reverse=True)

    def snapshot(self, p, label):
        key = uid()
        content = {k: copy.deepcopy(p[k]) for k in
                   ('title', 'source', 'template', 'duration', 'ratio', 'answers', 'questions', 'plan', 'assets', 'shots', 'approvals', 'phase',
                    'planning_stage', 'questions_stage', 'style_asset_id', 'trial', 'batch_limit', '_answered')}
        atomic(self.folder(p['id']) / 'history' / (key + '.json'), content)
        p['history'].append({'id': key, 'label': label, 'created': time.time()})

    def restore(self, p, key):
        if key not in {item['id'] for item in p['history']}:
            raise ValueError('版本不存在')
        content = json.loads((self.folder(p['id']) / 'history' / (identifier(key) + '.json')).read_text(encoding='utf-8'))
        self.snapshot(p, '恢复前的版本')
        # A restore selects prior content, never deletes more recent media.
        for group in ('assets', 'shots'):
            newer = {e['id']: e for e in p[group]}
            for entity in content[group]:
                known = {v['id'] for v in entity['versions']}
                entity['versions'].extend(v for v in newer.get(entity['id'], {}).get('versions', []) if v['id'] not in known)
            restored_ids = {e['id'] for e in content[group]}
            archive = p.setdefault('media_archive', [])
            known_archived = {v['id'] for v in archive}
            for key, entity in newer.items():
                if key not in restored_ids:
                    for v in entity['versions']:
                        if v['id'] not in known_archived:
                            archive.append(dict(v, entity_id=key, entity_label=entity.get('name', entity.get('title', key))))
                            known_archived.add(v['id'])
        p.update(content)
        p['approvals'] = dict(plan=False, assets=False, storyboard=False)
        p['trial'].update(approved=False, version_id=None)
        p['phase'] = 'plan' if p['plan'] else 'idea'
        p['_quotes'] = {}
        p['_proposals'] = {}
        return p

    def file(self, pid, relative):
        if not isinstance(relative, str) or '\\' in relative:
            raise ValueError('无效素材路径')
        base = self.folder(pid).resolve()
        path = base / relative
        resolved = path.resolve()
        if not resolved.is_relative_to(base) or path.is_symlink() or not path.is_file():
            raise ValueError('素材不存在')
        return resolved


def public_project(p):
    result = copy.deepcopy(p)
    for key in list(result):
        if key.startswith('_'):
            del result[key]
    for job in result.get('jobs', []):
        for key in list(job):
            if key.startswith('_'):
                del job[key]
    return result


def revision(p, value):
    if type(value) is not int or value != p['revision']:
        raise ValueError('内容已发生变化，请刷新后再确认，避免覆盖新版本')
