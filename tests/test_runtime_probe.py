"""Exercise the production Swift probe against controlled HTTP sources."""
import http.server
import hashlib
import io
import os
import tarfile
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.ranges.append(self.headers.get('Range'))
        time.sleep(self.server.delay)
        if self.server.fail:
            self.send_error(503)
            return
        self.send_response(206)
        self.send_header('Content-Length', '262144')
        self.send_header('Content-Range', 'bytes 0-262143/999999')
        self.end_headers()
        try:
            self.wfile.write(b'x' * 262144)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


class RuntimeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / 'probe'
        source = (ROOT / 'scripts/native_bootstrap.swift').read_text().split('final class Bootstrap:')[0]
        source += '''
let sources = Array(CommandLine.arguments.dropFirst())
let ranked = try probeRuntimeSources(sources, path: "runtime", checkPaused: {}, report: { print($0) })
print(String(data: try JSONEncoder().encode(ranked), encoding: .utf8)!)
'''
        swift = cls.binary.with_suffix('.swift')
        swift.write_text(source)
        subprocess.run(['/usr/bin/swiftc', str(swift), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def server(self, delay=0, fail=False):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.delay, server.fail, server.ranges = delay, fail, []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f'http://127.0.0.1:{server.server_port}/'

    def probe(self, sources):
        start = time.monotonic()
        result = subprocess.check_output([str(self.binary), *sources], text=True, timeout=13)
        return json.loads(result.splitlines()[-1]), time.monotonic() - start

    def test_hf_unreachable_modelscope_chosen_quickly(self):
        ms, url = self.server()
        ranked, elapsed = self.probe(['http://127.0.0.1:1/', url])
        self.assertEqual(ranked, [url, 'http://127.0.0.1:1/'])
        self.assertLess(elapsed, 2)
        self.assertEqual(ms.ranges, ['bytes=0-262143'])

    def test_both_reachable_faster_wins_in_either_position(self):
        slow, slow_url = self.server(delay=0.7)
        fast, fast_url = self.server(delay=0.05)
        for sources in ([slow_url, fast_url], [fast_url, slow_url]):
            ranked, elapsed = self.probe(sources)
            self.assertEqual(ranked, [fast_url, slow_url])
            self.assertLess(elapsed, 1.5)
        self.assertEqual(slow.ranges, ['bytes=0-262143'] * 2)
        self.assertEqual(fast.ranges, ['bytes=0-262143'] * 2)

    def test_stalled_hf_bounded_by_total_timeout(self):
        _, stalled = self.server(delay=11)
        _, reachable = self.server()
        ranked, elapsed = self.probe([stalled, reachable])
        self.assertEqual(ranked, [reachable, stalled])
        self.assertLess(elapsed, 10.8)

    def test_failed_sources_retained_for_fallback(self):
        _, failed = self.server(fail=True)
        _, reachable = self.server()
        ranked, _ = self.probe([failed, reachable])
        self.assertEqual(ranked, [reachable, failed])


class RuntimeInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / 'install'
        source = (ROOT / 'scripts/native_bootstrap.swift').read_text().split('let application = NSApplication.shared')[0]
        source += r'''
let bootstrap = Bootstrap()
bootstrap.home = URL(fileURLWithPath: CommandLine.arguments[1])
bootstrap.entry = Release.Entry(path: "runtime", size: Int64(CommandLine.arguments[2])!, sha256: CommandLine.arguments[3])
bootstrap.runtime = bootstrap.home.appendingPathComponent("runtimes/" + bootstrap.entry.sha256)
bootstrap.runtimeSources = Array(CommandLine.arguments.dropFirst(4))
try bootstrap.installRuntime()
print("installed")
'''
        swift = cls.binary.with_suffix('.swift')
        swift.write_text(source)
        subprocess.run(['/usr/bin/swiftc', str(swift), '-o', str(cls.binary)], check=True)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w:gz') as bundle:
            for name, data, mode in [('bin/python3', b'#!/bin/sh\nexit 0\n', 0o755),
                                     ('padding', os.urandom(400000), 0o644)]:
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                bundle.addfile(info, io.BytesIO(data))
        cls.archive = archive.getvalue()
        cls.sha = hashlib.sha256(cls.archive).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def server(self, fail_transfer=False, corrupt=False):
        archive = self.archive
        class TransferHandler(Handler):
            def do_GET(self):
                requested = self.headers.get('Range')
                self.server.ranges.append(requested)
                if requested == 'bytes=0-262143':
                    start, end = 0, 262143
                    if not fail_transfer and not corrupt:
                        time.sleep(0.1)
                else:
                    if fail_transfer:
                        self.send_error(503)
                        return
                    start = int(requested.split('=')[1].split('-')[0]) if requested else 0
                    end = len(archive) - 1
                body = archive[start:end + 1]
                if corrupt and requested != 'bytes=0-262143':
                    body = b'x' * len(body)
                self.send_response(206 if requested else 200)
                self.send_header('Content-Length', str(len(body)))
                if requested:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(archive)}')
                self.end_headers()
                self.wfile.write(body)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), TransferHandler)
        server.ranges = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f'http://127.0.0.1:{server.server_port}/'

    def install(self, home, sources):
        (home / 'work').mkdir(exist_ok=True)
        subprocess.run([str(self.binary), str(home), str(len(self.archive)), self.sha, *sources],
                       check=True, capture_output=True, text=True, timeout=20)
        self.assertEqual((home / 'runtimes' / self.sha / '.complete').read_text(), self.sha)
        self.assertEqual(hashlib.sha256((home / 'runtime-archives' / (self.sha + '.tar.gz')).read_bytes()).hexdigest(), self.sha)

    def test_resume_and_cached_offline_restart(self):
        server, url = self.server()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            archives = home / 'runtime-archives'
            archives.mkdir()
            (archives / (self.sha + '.part')).write_bytes(self.archive[:300000])
            self.install(home, [url])
            self.assertIn('bytes=300000-', server.ranges)
            count = len(server.ranges)
            self.install(home, ['http://127.0.0.1:1/'])
            self.assertEqual(len(server.ranges), count)

    def test_failed_transfer_falls_back(self):
        bad, bad_url = self.server(fail_transfer=True)
        good, good_url = self.server()
        with tempfile.TemporaryDirectory() as directory:
            self.install(Path(directory), [bad_url, good_url])
        self.assertEqual(bad.ranges.count(None), 2)
        self.assertIn(None, good.ranges)

    def test_sha_mismatch_falls_back(self):
        bad, bad_url = self.server(corrupt=True)
        good, good_url = self.server()
        with tempfile.TemporaryDirectory() as directory:
            self.install(Path(directory), [bad_url, good_url])
        self.assertEqual(bad.ranges.count(None), 2)
        self.assertIn(None, good.ranges)
