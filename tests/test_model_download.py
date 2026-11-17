"""Local HTTP integration coverage. Every download stays inside TemporaryDirectory."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_download import Downloader, RuntimeDownloader, extract_runtime, validate_manifest, MODEL_SHA


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {'model.safetensors': bytes(range(256)) * 8192, 'config.json': b'{"r3":8}'}
        self.requests = []
        self.mode = 'normal'
        self.slow = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split('/')[-1]
                data = owner.data.get(path)
                range_value = self.headers.get('Range', '')
                owner.requests.append((self.path, range_value))
                if self.path.startswith('/bad/') or data is None:
                    self.send_error(503)
                    return
                start = int(range_value.split('=')[1].split('-')[0]) if range_value else 0
                end_value = range_value.split('-')[-1] if range_value else ''
                end = min(int(end_value), len(data)-1) if end_value else len(data)-1
                if owner.mode == 'ignore':
                    start, end = 0, len(data)-1
                    range_value = ''
                body = data[start:end+1]
                if owner.mode == 'corrupt' or (owner.mode == 'corrupt-first' and self.path.startswith('/one/')):
                    body = bytes([body[0] ^ 1]) + body[1:]
                self.send_response(206 if range_value else 200)
                if range_value:
                    offset = start + 1 if owner.mode == 'bad-range' else start
                    self.send_header('Content-Range', f'bytes {offset}-{end}/{len(data)}')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if owner.mode == 'interrupt' and not end_value:
                    body = body[:262144]
                try:
                    for offset in range(0, len(body), 16384):
                        self.wfile.write(body[offset:offset+16384])
                        self.wfile.flush()
                        if owner.slow:
                            time.sleep(.004)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                if owner.mode == 'interrupt' and not end_value:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.manifest = dict(files=[dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
                                   for name, data in self.data.items()])

    def downloader(self, sources=None):
        return Downloader(self.root / 'home', self.manifest,
                          sources or {'one': self.base + '/one/', 'two': self.base + '/two/'}, require_r3=False)

    def wait(self, d):
        d._thread.join(15)
        self.assertFalse(d._thread.is_alive())
        return d.status()

    def prepared(self, d):
        d.prepare()
        self.assertEqual(self.wait(d)['status'], 'prepared')

    def complete(self, d):
        d.start()
        state = self.wait(d)
        self.assertEqual(state['status'], 'done', state)
        for name, data in self.data.items():
            self.assertEqual((d.target / name).read_bytes(), data)

    def test_probe_then_verified_activation_and_offline_relaunch(self):
        d = self.downloader()
        self.prepared(d)
        print('LOCAL_PROBES', json.dumps(d.status()['probes']))
        self.assertFalse(d.target.exists())
        self.assertTrue(all(value == 'bytes=0-262143' for _, value in self.requests))
        self.complete(d)
        self.requests.clear()
        d.prepare()
        self.assertTrue(self.wait(d)['ready'])
        self.assertEqual(self.requests, [])

    def test_range_resume_after_restart(self):
        d = self.downloader()
        d.stage.mkdir(parents=True)
        (d.stage / 'model.safetensors.part').write_bytes(self.data['model.safetensors'][:131072])
        d = self.downloader()
        self.prepared(d)
        self.complete(d)
        self.assertTrue(any(value == 'bytes=131072-' for _, value in self.requests))

    def test_pause_and_resume(self):
        self.slow = True
        d = self.downloader()
        self.prepared(d)
        d.start()
        deadline = time.monotonic() + 5
        while d.status()['downloaded'] == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        d.pause()
        self.assertEqual(self.wait(d)['status'], 'paused')
        self.assertFalse(d.target.exists())
        self.complete(d)
        self.assertTrue(any(value.endswith('-') for _, value in self.requests if value))

    def test_interrupted_download_then_restart(self):
        d = self.downloader()
        self.prepared(d)
        self.mode = 'interrupt'
        d.start()
        self.assertEqual(self.wait(d)['status'], 'error')
        self.assertFalse(d.target.exists())
        self.mode = 'normal'
        d = self.downloader()
        self.prepared(d)
        self.complete(d)
        self.assertTrue(any(value.startswith('bytes=1048576-') for _, value in self.requests))

    def test_sha_mismatch_redownload_and_source_fallback(self):
        d = self.downloader()
        self.prepared(d)
        d._ranked = ['one', 'two']
        self.mode = 'corrupt-first'
        self.complete(d)
        self.assertEqual(d.status()['source'], 'two')
        self.assertGreaterEqual(sum(path == '/one/model.safetensors' and not value
                                    for path, value in self.requests), 2)

    def test_all_sources_corrupt_never_activates(self):
        d = self.downloader()
        self.prepared(d)
        self.mode = 'corrupt'
        d.start()
        self.assertEqual(self.wait(d)['status'], 'error')
        self.assertFalse(d.target.exists())

    def test_unreachable_source_fallback(self):
        d = self.downloader({'bad': self.base + '/bad/', 'good': self.base + '/two/'})
        self.prepared(d)
        self.assertEqual(d.status()['source'], 'good')
        self.complete(d)

    def test_transfer_failure_falls_back_after_successful_probe(self):
        d = self.downloader()
        self.prepared(d)
        d.sources['one'] = self.base + '/bad/'
        d._ranked = ['one', 'two']
        self.complete(d)

    def test_server_ignores_range_restarts_safely(self):
        d = self.downloader()
        d.stage.mkdir(parents=True)
        (d.stage / 'model.safetensors.part').write_bytes(b'x' * 1234)
        self.prepared(d)
        self.mode = 'ignore'
        self.complete(d)

    def test_invalid_range_is_rejected(self):
        d = self.downloader()
        d.stage.mkdir(parents=True)
        (d.stage / 'model.safetensors.part').write_bytes(b'x' * 1234)
        self.prepared(d)
        self.mode = 'bad-range'
        d.start()
        self.assertEqual(self.wait(d)['status'], 'error')
        self.assertFalse(d.target.exists())

    def test_fully_cached_restart_activates_offline(self):
        d = self.downloader()
        d.stage.mkdir(parents=True)
        for name, data in self.data.items():
            (d.stage / (name + '.part')).write_bytes(data)
        with patch.object(d, '_probe', side_effect=AssertionError('Unexpected network probe')):
            self.prepared(d)
        self.complete(d)
        self.assertEqual(self.requests, [])

    def test_faster_reachable_source_is_preferred(self):
        d = self.downloader()
        def probe(name, base, entry):
            return dict(name=name, reachable=True, speed=100 if name == 'two' else 10)
        with patch.object(d, '_probe', side_effect=probe):
            self.prepared(d)
        self.assertEqual(d.status()['source'], 'two')

    def test_repair_redownloads_only_invalid_file(self):
        d = self.downloader()
        self.prepared(d)
        self.complete(d)
        (d.target / 'config.json').write_bytes(b'corrupted')
        self.requests.clear()
        self.prepared(d)
        self.complete(d)
        downloads = [path for path, value in self.requests if value != 'bytes=0-262143']
        self.assertEqual(downloads, ['/one/config.json'] if d.status()['source'] == 'one'
                         else ['/two/config.json'])

    def test_process_lock_preserves_partial_files(self):
        import fcntl
        d = self.downloader()
        self.prepared(d)
        d.stage.mkdir(parents=True)
        with (d.stage / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            d.start()
            self.assertEqual(self.wait(d)['status'], 'error')
            self.assertFalse(d.target.exists())
        self.complete(d)

    def test_activation_failure_preserves_existing_directory(self):
        d = self.downloader()
        self.prepared(d)
        self.complete(d)
        (d.target / 'config.json').write_bytes(b'previous')
        self.prepared(d)
        rename = Path.rename
        def fail_ready(path, target):
            if path.name == 'verified':
                raise OSError('Simulated activation failure')
            return rename(path, target)
        with patch.object(Path, 'rename', fail_ready):
            d.start()
            self.assertEqual(self.wait(d)['status'], 'error')
        self.assertEqual((d.target / 'config.json').read_bytes(), b'previous')
        self.complete(d)

    def test_manifest_pin_and_path_validation(self):
        with self.assertRaises(ValueError):
            validate_manifest(self.manifest)
        for path in ['../escape', '/absolute', 'a/../b', 'a\\b', './config']:
            with self.assertRaises(ValueError):
                validate_manifest(dict(files=[dict(path=path, size=1, sha256=MODEL_SHA)]), False)

    def test_runtime_archive_download_verification_extraction(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as bundle:
            info = tarfile.TarInfo('bin/python3')
            info.size = 5
            info.mode = 0o755
            bundle.addfile(info, io.BytesIO(b'test!'))
        self.data = {'runtime.tar.gz': stream.getvalue()}
        manifest = dict(files=[dict(path='runtime.tar.gz', size=len(stream.getvalue()),
                                   sha256=hashlib.sha256(stream.getvalue()).hexdigest())])
        d = RuntimeDownloader(self.root / 'home', manifest, {'local': self.base + '/one/'})
        self.prepared(d)
        self.complete(d)
        binary = next((self.root / 'home/runtimes').glob('*/bin/python3'))
        self.assertEqual(binary.read_bytes(), b'test!')
        binary.unlink()
        # Simulate a partial runtime directory left by an interrupted older release.
        binary.parent.rmdir()
        binary.parent.parent.rmdir()
        self.requests.clear()
        d = RuntimeDownloader(self.root / 'home', manifest, {'local': self.base + '/one/'})
        self.prepared(d)
        self.complete(d)
        self.assertTrue(binary.is_file())
        self.assertEqual(self.requests, [])

    def test_release_preparation_generates_commands_without_upload(self):
        import runpy
        model = self.root / 'model files'
        model.mkdir()
        (model / 'model.safetensors').write_bytes(b'fixture weights')
        (model / 'config.json').write_bytes(b'{}')
        runtime = self.root / 'runtime.tar.gz'
        runtime.write_bytes(b'fixture archive')
        output = self.root / 'release'
        script = Path(__file__).resolve().parents[1] / 'scripts/prepare_release_upload.py'
        def fixture_sha(path):
            return MODEL_SHA if Path(path).name == 'model.safetensors' else hashlib.sha256(Path(path).read_bytes()).hexdigest()
        with patch('model_download.sha256', side_effect=fixture_sha), patch.object(sys, 'argv',
                [str(script), '--model', str(model), '--output', str(output), '--runtime', str(runtime)]):
            runpy.run_path(str(script), run_name='__main__')
        manifest = json.loads((output / 'model_manifest.json').read_text())
        self.assertEqual(len(manifest['files']), 2)
        self.assertEqual(len(json.loads((output / 'runtime_manifest.json').read_text())['files']), 1)
        commands = (output / 'UPLOAD.md').read_text()
        self.assertEqual(commands.count('hf upload '), 5)
        self.assertEqual(commands.count('ms-hub upload '), 5)
        self.assertIn("'" + str(model / 'model.safetensors') + "'", commands)

    def test_runtime_archive_links_rejected(self):
        archive = self.root / 'link.tar'
        with tarfile.open(archive, 'w') as bundle:
            info = tarfile.TarInfo('python')
            info.type = tarfile.SYMTYPE
            info.linkname = '/outside'
            bundle.addfile(info)
        with self.assertRaises(ValueError):
            extract_runtime(archive, self.root / 'extract')
        self.assertFalse((self.root / 'extract').exists())

    def test_runtime_archive_traversal_rejected(self):
        archive = self.root / 'unsafe.tar'
        with tarfile.open(archive, 'w') as bundle:
            info = tarfile.TarInfo('../escape')
            info.size = 1
            bundle.addfile(info, io.BytesIO(b'x'))
        with self.assertRaises(ValueError):
            extract_runtime(archive, self.root / 'extract')
        self.assertFalse((self.root / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
