import base64
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

from app.server import handler_for
from app.studio import Studio


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.studio = Studio(Path(self.temp.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
        self.port = self.server.server_port
        self.server.RequestHandlerClass = handler_for(self.studio, self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        code, body, _ = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.token = re.search(r'name="studio-token" content="([^"]+)"', body.decode()).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            raw = json.dumps(data).encode() if data is not None else None
            request_headers = {'Content-Type': 'application/json', 'X-Studio-Token': getattr(self, 'token', '')}
            request_headers.update(headers or {})
            conn.request(method, path, body=raw, headers=request_headers)
            response = conn.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            conn.close()

    def test_status_and_independent_service_id(self):
        code, body, _ = self.request('GET', '/api/status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['app'], 'idea-to-video')
        self.assertFalse(json.loads(body)['text_configured'])

    def test_host_rebinding_and_cross_origin_rejected(self):
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}):
            self.assertEqual(self.request('GET', '/api/projects', headers=headers)[0], 403)

    def test_csrf_token_and_content_type(self):
        data = {'source': '文字'}
        for headers in ({'X-Studio-Token': 'wrong'}, {'Content-Type': 'text/plain'}):
            self.assertIn(self.request('POST', '/api/projects', data, headers)[0], (400, 403))
        self.assertEqual(self.studio.store.list(), [])

    def test_create_then_unconfigured_analysis_honest(self):
        code, body, _ = self.request('POST', '/api/projects', {'source': '我提供的红色杯子', 'template': 'product'})
        self.assertEqual(code, 201)
        p = json.loads(body)
        code, body, _ = self.request('POST', f'/api/projects/{p["id"]}/analyze', {'expected_revision': p['revision'], 'stage': 'plan'})
        self.assertEqual(code, 400)
        self.assertIn('尚未配置', json.loads(body)['error'])
        self.assertIsNone(self.studio.store.read(p['id'])['plan'])

    def test_keys_never_returned_and_files_not_served(self):
        code, _, _ = self.request('POST', '/api/config', {'base_url': 'https://example.invalid/v1', 'model': 'test', 'api_key': 'private-testing-key'})
        self.assertEqual(code, 200)
        _, body, _ = self.request('GET', '/api/config')
        self.assertNotIn('private-testing-key', body.decode())
        for path in ('/.local/config.json', '/app/planner.py', '/media/no/../../.local/config.json'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_media_allowlist_ranges_head_and_traversal(self):
        p = self.studio.create({'source': '测试'})
        p['assets'] = [{'id': 'asset', 'versions': [{'id': 'v', 'url': f'/media/{p["id"]}/uploads/test.png'}]}]
        folder = self.studio.store.folder(p['id']) / 'uploads'
        folder.mkdir(parents=True)
        (folder / 'test.png').write_bytes(b'0123456789')
        self.studio.store.save(p)
        url = f'/media/{p["id"]}/uploads/test.png'
        code, body, _ = self.request('GET', url, headers={'Range': 'bytes=2-4'})
        self.assertEqual((code, body), (206, b'234'))
        code, body, headers = self.request('HEAD', url)
        self.assertEqual(code, 200)
        self.assertEqual(body, b'')
        self.assertEqual(headers['Content-Length'], '10')
        self.assertEqual(self.request('GET', url, headers={'Range': 'bytes=20-'})[0], 416)
        self.assertEqual(self.request('GET', f'/media/{p["id"]}/project.json')[0], 404)
        self.assertEqual(self.request('GET', f'/media/{p["id"]}/uploads/../project.json')[0], 404)


if __name__ == '__main__':
    unittest.main()
