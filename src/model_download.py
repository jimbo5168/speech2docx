"""Resumable release downloads, using only the Python standard library."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import threading
import time
from urllib.request import Request, urlopen
from urllib.parse import quote

MODEL_NAME = 'qwen3-asr-1.7b-buddhist-8bit'
MODEL_SHA = '0f7b16c8adde1b941624d97281afd08d2b7e10e38ecb9e01aabc878060a91085'
# All public source configuration lives here. Both repositories must contain identical files.
REPOSITORIES = {
    'Hugging Face': 'jimbo5168/weiwowensi-asr-r3-8bit',
    'ModelScope': 'jimbo5168/weiwowensi-asr-r3-8bit',
}
SOURCE_TEMPLATES = {
    'Hugging Face': 'https://huggingface.co/{repo}/resolve/main/',
    'ModelScope': 'https://modelscope.cn/models/{repo}/resolve/master/',
}
MANIFEST_PATH = Path(__file__).with_name('model_manifest.json')
CHUNK = 64 * 1024


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_manifest(manifest, require_r3=True):
    files = manifest['files']
    names = set()
    if not files:
        raise ValueError('Empty release manifest')
    for entry in files:
        name = entry['path']
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
                or str(path) != name or name in names):
            raise ValueError('Unsafe or duplicate release path')
        names.add(name)
        if type(entry['size']) is not int or entry['size'] <= 0:
            raise ValueError('Invalid release size')
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('Invalid release SHA256')
    if require_r3:
        weights = next((f for f in files if f['path'] == 'model.safetensors'), None)
        if not weights or weights['sha256'] != MODEL_SHA:
            raise ValueError('Release must use the pinned r3 8-bit weights')
    return manifest


class Paused(Exception):
    pass


class Downloader:
    def __init__(self, home, manifest=None, sources=None, require_r3=True):
        self.home = Path(home)
        self.manifest = validate_manifest(
            manifest if manifest is not None else json.loads(MANIFEST_PATH.read_text()), require_r3)
        self.sources = sources or {
            name: SOURCE_TEMPLATES[name].format(repo=repo)
            for name, repo in REPOSITORIES.items()
        }
        self.target = self.home / 'models' / MODEL_NAME
        self.stage = self.home / 'work' / 'model-download'
        self.total = sum(f['size'] for f in self.manifest['files'])
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._ranked = []
        self._state = dict(status='idle', total=self.total, downloaded=0, speed=0,
                           remaining=None, probes=[], source='', error='', ready=False)

    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    def status(self):
        with self._lock:
            return dict(self._state)

    def prepare(self):
        """Hash installed files before trusting them. Probe only if installation is needed."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._thread = threading.Thread(target=self._prepare, daemon=True)
            self._thread.start()
        return self.status()

    def _valid(self, path, entry):
        return (path.is_file() and not path.is_symlink()
                and path.stat().st_size == entry['size'] and sha256(path) == entry['sha256'])

    def _installed_valid(self):
        return all(self._valid(self.target / f['path'], f) for f in self.manifest['files'])

    def _prepare(self):
        try:
            self._update(status='checking', error='', ready=False)
            if self._installed_valid():
                self._update(status='done', ready=True, downloaded=self.total, remaining=0)
                return
            cached = all(self._valid(self.stage / (f['path'] + '.part'), f)
                         or self._valid(self.target / f['path'], f) for f in self.manifest['files'])
            if cached:
                self._ranked = list(self.sources)
                self._update(status='prepared', downloaded=self.total, source='本机缓存',
                             speed=0, remaining=0, probes=[])
                return
            self._update(status='probing', downloaded=self._downloaded())
            probe_file = max(self.manifest['files'], key=lambda f: f['size'])
            with ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
                probes = list(pool.map(lambda item: self._probe(*item, probe_file), self.sources.items()))
            self._ranked = [p['name'] for p in sorted(probes, key=lambda p: p['speed'], reverse=True)
                            if p['reachable']]
            best = next((p for p in probes if self._ranked and p['name'] == self._ranked[0]), None)
            self._update(status='prepared' if best else 'error', probes=probes,
                         source=best['name'] if best else '', speed=best['speed'] if best else 0,
                         remaining=self.total / best['speed'] if best else None,
                         error='' if best else '下载源暂不可用，请稍后重新测速。')
        except Exception as exc:
            self._update(status='error', error=str(exc))

    def _probe(self, name, base, entry):
        start = time.monotonic()
        count = 0
        try:
            request = Request(base + quote(entry['path'], safe='/'), headers={'Range': 'bytes=0-262143',
                                                           'Accept-Encoding': 'identity'})
            with urlopen(request, timeout=2) as response:
                while count < 262144 and time.monotonic() - start < 3:
                    block = response.read(min(CHUNK, 262144 - count))
                    if not block:
                        break
                    count += len(block)
            if not count:
                raise OSError('Empty probe')
            elapsed = time.monotonic() - start
            return dict(name=name, reachable=True, bytes=count, seconds=elapsed,
                        speed=count / elapsed, error='')
        except Exception as exc:
            return dict(name=name, reachable=False, bytes=count, seconds=time.monotonic() - start,
                        speed=0, error=str(exc))

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self._state['ready']:
                return self.status()
            if not self._ranked:
                raise ValueError('Probe sources before starting the download')
            self._stop.clear()
            self._update(status='downloading', error='')
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self.status()

    def pause(self):
        self._stop.set()
        return self.status()

    def _check_pause(self):
        if self._stop.is_set():
            raise Paused()

    def _downloaded(self):
        return sum(min((self.stage / (f['path'] + '.part')).stat().st_size, f['size'])
                         for f in self.manifest['files']
                         if (self.stage / (f['path'] + '.part')).is_file())

    def _progress(self):
        downloaded = self._downloaded()
        elapsed = max(time.monotonic() - self._started, .001)
        speed = self._received / elapsed
        self._update(downloaded=downloaded, speed=speed,
                     remaining=(self.total - downloaded) / speed if speed else None)

    def _transfer(self, entry, base):
        part = self.stage / (entry['path'] + '.part')
        part.parent.mkdir(parents=True, exist_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        if offset > entry['size']:
            part.unlink()
            offset = 0
        if offset == entry['size']:
            return
        headers = {'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        with urlopen(Request(base + quote(entry['path'], safe='/'), headers=headers), timeout=5) as response:
            if response.status == 206:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                if (not match or int(match[1]) != offset or int(match[3]) != entry['size']
                        or int(match[2]) != entry['size'] - 1):
                    raise OSError('Invalid Content-Range')
            elif response.status == 200:
                offset = 0  # A server ignoring Range must restart, never append.
            else:
                raise OSError(f'Unexpected HTTP status {response.status}')
            with part.open('ab' if offset else 'wb') as output:
                while True:
                    self._check_pause()
                    block = response.read(min(CHUNK, entry['size'] - output.tell() + 1))
                    if not block:
                        break
                    if output.tell() + len(block) > entry['size']:
                        raise OSError('Response exceeds manifest size')
                    output.write(block)
                    output.flush()
                    self._received += len(block)
                    self._progress()
                os.fsync(output.fileno())
        if part.stat().st_size != entry['size']:
            raise OSError('Interrupted download')

    def _run(self):
        try:
            self.stage.mkdir(parents=True, exist_ok=True)
            self._run_locked()
        except Exception as exc:
            self._update(status='error', error=str(exc), speed=0)

    def _run_locked(self):
        # One writer across windows/processes. The OS releases this lock on a crash.
        with (self.stage / '.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._started = time.monotonic()
                self._received = 0
                self._progress()
                for entry in self.manifest['files']:
                    self._check_pause()
                    part = self.stage / (entry['path'] + '.part')
                    if self._valid(part, entry):
                        continue
                    # Preserve already-installed valid files when repairing an installation.
                    if self._valid(self.target / entry['path'], entry):
                        part.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(self.target / entry['path'], part)
                        continue
                    errors = []
                    for name in self._ranked + [n for n in self.sources if n not in self._ranked]:
                        for attempt in range(2):
                            self._check_pause()
                            self._update(status='downloading', source=name)
                            try:
                                self._transfer(entry, self.sources[name])
                                self._update(status='verifying')
                                if not self._valid(part, entry):
                                    part.unlink(missing_ok=True)
                                    raise OSError('SHA256 mismatch: ' + entry['path'])
                                break
                            except Paused:
                                raise
                            except Exception as exc:
                                errors.append(f'{name}: {exc}')
                        else:
                            continue
                        break
                    else:
                        raise OSError(' / '.join(errors))
                self._check_pause()
                self._update(status='verifying', downloaded=self.total)
                for entry in self.manifest['files']:
                    if not self._valid(self.stage / (entry['path'] + '.part'), entry):
                        raise OSError('Final verification failed: ' + entry['path'])
                self._activate()
                self._update(status='done', ready=True, remaining=0, speed=0)
            except Paused:
                self._update(status='paused', speed=0, remaining=None)
            except Exception as exc:
                self._update(status='error', error=str(exc), speed=0)

    def _activate(self):
        """Only the fully verified directory is made visible to inference."""
        ready = self.stage / 'verified'
        if ready.exists():
            shutil.rmtree(ready)
        ready.mkdir()
        for entry in self.manifest['files']:
            dest = ready / entry['path']
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Keep .part files for recovery until activation completes.
            os.link(self.stage / (entry['path'] + '.part'), dest)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        backup = self.target.with_name(self.target.name + '.previous')
        if backup.exists() and self.target.exists():
            shutil.rmtree(backup)
        if self.target.exists():
            self.target.rename(backup)
        try:
            ready.rename(self.target)
        except Exception:
            if backup.exists() and not self.target.exists():
                backup.rename(self.target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        for entry in self.manifest['files']:
            (self.stage / (entry['path'] + '.part')).unlink(missing_ok=True)


def extract_runtime(archive, destination):
    """Extract a verified portable runtime into a new directory, rejecting links and traversal."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Runtime destination already exists')
    destination.mkdir(parents=True)
    try:
        with tarfile.open(archive, 'r:*') as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe runtime archive member')
                output = destination.joinpath(*path.parts)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, output.open('wb') as target:
                        shutil.copyfileobj(source, target)
                    output.chmod(member.mode & 0o755)
    except Exception:
        shutil.rmtree(destination)
        raise


class RuntimeDownloader(Downloader):
    """Use a bundled, pinned runtime manifest with the same probes, Range and SHA checks.

    A release bootstrap can call this without importing GUI/ML dependencies. The
    archive must be a self-contained, relocatable macOS arm64 distribution.
    """
    def __init__(self, home, manifest, sources=None):
        super().__init__(home, manifest, sources, require_r3=False)
        if len(self.manifest['files']) != 1:
            raise ValueError('Runtime release must contain one archive')
        self.target = self.home / 'runtime-archives'
        self.stage = self.home / 'work' / 'runtime-download'

    def _installed_valid(self):
        runtime = self.home / 'runtimes' / self.manifest['files'][0]['sha256']
        return super()._installed_valid() and (runtime / 'bin' / 'python3').is_file()

    def _activate(self):
        archive = self.stage / (self.manifest['files'][0]['path'] + '.part')
        prepared = self.stage / 'extracted'
        if prepared.exists():
            shutil.rmtree(prepared)
        extract_runtime(archive, prepared)
        if not (prepared / 'bin' / 'python3').is_file():
            raise ValueError('Runtime archive must contain bin/python3')
        # Version by digest so concurrent launches can retain their active runtime.
        runtime = self.home / 'runtimes' / self.manifest['files'][0]['sha256']
        runtime.parent.mkdir(parents=True, exist_ok=True)
        if not (runtime / 'bin' / 'python3').is_file():
            if runtime.exists():
                shutil.rmtree(runtime)
            prepared.rename(runtime)
        else:
            shutil.rmtree(prepared)
        super()._activate()
