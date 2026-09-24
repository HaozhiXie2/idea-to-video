"""Loopback-only HTTP service. Adapted request/range safety from novel-storyboard (MIT)."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import unquote, urlsplit

if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.studio import Studio
from app.store import public_project

ROOT = Path(__file__).resolve().parents[1]


def handler_for(studio, port=7862):
    token = secrets.token_urlsafe(32)
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'IdeaStudio/1'

        def log_message(self, fmt, *args):
            # No request bodies, prompts, keys, or query strings in logs.
            pass

        def valid_origin(self):
            return self.headers.get('Host') in hosts and self.headers.get('Origin') in (None, *(f'http://{host}' for host in hosts))

        def send(self, data, kind='application/json; charset=utf-8', code=200):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def file(self, path):
            total = path.stat().st_size
            start, end, code = 0, total - 1, 200
            byte_range = self.headers.get('Range')
            if byte_range:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', byte_range)
                if not match or not any(match.groups()):
                    return self.send({'error': '不支持的文件范围'}, code=416)
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), total - 1) if right else total - 1
                else:
                    start = max(0, total - int(right))
                if start > end or start >= total:
                    return self.send({'error': '文件范围超出边界'}, code=416)
                code = 206
            self.send_response(code)
            self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(max(0, end - start + 1)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if code == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{total}')
            self.end_headers()
            if self.command == 'HEAD':
                return
            try:
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = stream.read(min(remaining, 65536))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.valid_origin():
                return self.send({'error': '仅支持本机同源访问'}, code=403)
            route = urlsplit(self.path).path
            try:
                if route == '/':
                    return self.send((ROOT / 'app/index.html').read_text(encoding='utf-8').replace('__TOKEN__', token).encode(), 'text/html; charset=utf-8')
                if route in ('/app.js', '/style.css'):
                    return self.send((ROOT / 'app' / route[1:]).read_bytes(), 'text/javascript; charset=utf-8' if route.endswith('.js') else 'text/css; charset=utf-8')
                if route == '/api/status':
                    return self.send(studio.status())
                if route == '/api/config':
                    return self.send(studio.planner.public())
                if route == '/api/projects':
                    return self.send(studio.store.list())
                parts = [unquote(p) for p in route.split('/')]
                if len(parts) == 4 and parts[1:3] == ['api', 'projects']:
                    return self.send(public_project(studio.store.read(parts[3])))
                if len(parts) >= 4 and parts[1] == 'media':
                    return self.file(studio.media_path(parts[2], '/'.join(parts[3:])))
                return self.send({'error': '页面不存在'}, code=404)
            except (ValueError, OSError):
                return self.send({'error': '项目或文件不存在，或记录无法读取'}, code=404)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if not self.valid_origin() or not secrets.compare_digest(self.headers.get('X-Studio-Token', ''), token):
                return self.send({'error': '页面验证已过期，请刷新后重试'}, code=403)
            try:
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('只接受 JSON 请求')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 12 * 1024 * 1024:
                    raise ValueError('请求大小超出限制')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确')
                route = urlsplit(self.path).path
                if route == '/api/config':
                    return self.send(studio.planner.save(data))
                if route == '/api/projects':
                    return self.send(studio.create(data), code=201)
                parts = route.split('/')
                if len(parts) == 5 and parts[1:3] == ['api', 'projects']:
                    pid, action = parts[3:]
                    if action == 'analyze':
                        result = studio.analyze(pid, data)
                    elif action == 'edit-proposal':
                        result = studio.propose(pid, data)
                    elif action == 'export':
                        result = studio.start_export(pid, data)
                    else:
                        result = studio.mutate(pid, action, data)
                    return self.send(result)
                return self.send({'error': '未知操作'}, code=404)
            except (ValueError, TypeError, KeyError) as exc:
                return self.send({'error': str(exc)[:2000]}, code=400)
            except Exception:
                return self.send({'error': '本机处理失败，已保留已有文件。请检查磁盘和安装状态；不会自动重试付费生成。'}, code=500)

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=7862)
    args = parser.parse_args()
    studio = Studio(ROOT)
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(studio, args.port))
    httpd.timeout = 5
    studio.startup_recover()
    threading.Thread(target=studio.worker, daemon=True).start()
    print(f'灵感影坊 http://127.0.0.1:{args.port}/', flush=True)
    try:
        httpd.serve_forever()
    finally:
        studio.stop_event.set()
        httpd.server_close()


if __name__ == '__main__':
    main()
