"""Explicit OFFLINE browser acceptance harness, never imported by production.

Run manually with --ffmpeg pointing to this project's installed binary.
All cloud calls are replaced with labelled deterministic fixtures. Port 7863,
temporary data, no credentials. Production stays on 7862, entirely separate.
"""
import argparse
import base64
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.media import MediaEngine
from app.server import handler_for
from app.studio import Studio
from app.store import uid


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x27\x60\x59' * 64) * 64)) + chunk(b'IEND', b''))


class FixturePlanner:
    def configured(self):
        return True

    def public(self):
        return dict(base_url='OFFLINE FIXTURE - NO NETWORK', model='deterministic-test', key_configured=False, json_mode=False)

    def analyze(self, p, stage):
        if stage == 'plan':
            if not p.get('_answered'):
                return {'questions': [{'id': 'q1', 'question': '离线验收：选择一个测试风格（不调用 AI）', 'options': ['测试纯色示意']} ]}
            return {'questions': [], 'plan': dict(summary='离线测试：三段纯色画面，仅用于验证流程，不是 AI 分析或成片。',
                    audience='开发验收', style='测试纯色示意', creative_notes=['所有云端调用均已替换为测试桩。'], beats=[])}
        if stage == 'assets':
            return {'questions': [], 'assets': []}
        return {'questions': [], 'shots': [dict(id='s'+str(i), title='离线测试镜头 '+str(i), source_excerpt='',
                description='测试纯色卡片，不是生成质量示例', action='测试固定画面', camera='固定', duration=5,
                asset_ids=[], image_prompt='离线测试图片', video_prompt='离线测试视频', versions=[],
                selected_image=None, selected_video=None, image_stale=True, video_stale=True) for i in range(1, 4)]}


class FixtureMedia(MediaEngine):
    def __init__(self, root, ffmpeg):
        super().__init__(root)
        self.ffmpeg = Path(ffmpeg)
        self.specs = {}

    def _ffmpeg(self):
        return self.ffmpeg

    def available(self):
        return True

    def submit(self, spec, directory):
        task = uid()
        self.specs[task] = spec
        return {'submit_id': task}

    def query(self, submit_id, directory, kind):
        directory.mkdir(parents=True, exist_ok=True)
        if kind == 'image':
            output = directory / 'offline-test.png'
            output.write_bytes(png())
        else:
            output = directory / 'offline-test.mp4'
            result = self._ffmpeg_run(['-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=0x276059:s=180x320:r=24',
                '-t', '5', '-vf', self._draft_filter().lstrip(','), '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-an', str(output)])
            if result.returncode:
                raise RuntimeError('offline fixture creation failed')
        return dict(status='done', message='离线测试桩完成（没有调用即梦）', files=[str(output)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='idea-browser-test-') as folder:
        root = Path(folder)
        studio = Studio(root, media=FixtureMedia(root, args.ffmpeg), planner=FixturePlanner())
        status = studio.status
        studio.status = lambda: dict(status(), test_mode=True)
        httpd = ThreadingHTTPServer(('127.0.0.1', 7863), handler_for(studio, 7863))
        def worker():
            while not studio.stop_event.wait(0.25):
                studio.worker_once()
        threading.Thread(target=worker, daemon=True).start()
        print('OFFLINE QA ONLY http://127.0.0.1:7863 - no cloud, no charges', flush=True)
        try:
            httpd.serve_forever()
        finally:
            studio.stop_event.set()
            httpd.server_close()


if __name__ == '__main__':
    main()
