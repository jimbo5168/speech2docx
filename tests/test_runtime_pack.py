import importlib.util
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import bootstrap_model

spec = importlib.util.spec_from_file_location('pack_runtime', ROOT / 'scripts/pack_runtime.py')
packer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packer)


class RuntimePackTests(unittest.TestCase):
    def test_links_materialized_and_console_script_relocates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'original'
            (root / 'bin').mkdir(parents=True)
            interpreter = root / 'bin/python-real'
            interpreter.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' \"$@\"\n')
            interpreter.chmod(0o755)
            (root / 'bin/python3').symlink_to('python-real')
            script = root / 'bin/tool'
            script.write_text('#!/old/path/python3\nprint("relocated")\n')
            script.chmod(0o755)
            archive = Path(temporary) / 'runtime.tar.gz'
            metadata = packer.pack(root, archive)
            destination = Path(temporary) / 'moved runtime'
            with tarfile.open(archive) as bundle:
                self.assertTrue(all(member.isfile() for member in bundle))
                self.assertEqual(sum(member.size for member in bundle), metadata['extracted_bytes'])
                bundle.extractall(destination, filter='data')
            result = subprocess.check_output([str(destination / 'bin/tool')], text=True)
            self.assertEqual(result.strip(), 'relocated')
            self.assertEqual(json.loads(archive.with_suffix('.gz.sizes.json').read_text()), metadata)

    def test_model_bridge_progress_and_activation(self):
        class Manager:
            def __init__(self, *args, **kwargs):
                self.started = False
            def prepare(self):
                pass
            def start(self):
                self.started = True
            def status(self):
                return dict(status='done' if self.started else 'prepared', ready=self.started)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict('os.environ', {'ASR_HOME': temporary}), patch.object(bootstrap_model, 'Downloader', Manager), patch.object(bootstrap_model.time, 'sleep'):
                bootstrap_model.main()
            state = json.loads((Path(temporary) / 'work/bootstrap-progress.json').read_text())
            self.assertTrue(state['ready'])

    def test_model_bridge_surfaces_failure(self):
        class Manager:
            def __init__(self, *args, **kwargs):
                pass
            def prepare(self):
                pass
            def status(self):
                return dict(status='error', ready=False, error='bad checksum')
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict('os.environ', {'ASR_HOME': temporary}), patch.object(bootstrap_model, 'Downloader', Manager):
                with self.assertRaisesRegex(RuntimeError, 'bad checksum'):
                    bootstrap_model.main()
