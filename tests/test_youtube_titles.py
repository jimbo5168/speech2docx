import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import subtitles
import template

class TitleTests(unittest.TestCase):
    def test_unbroken_titles(self):
        for title in ['HcFZT3T-63M', '这是一个没有自然分隔符的很长的中文标题']:
            self.assertEqual(template._title_lines(title), [title])

    def test_speaker_uses_title_only(self):
        self.assertEqual(template.infer_speaker('普通标题', [{'paragraphs': ['或者看到上师，看到佛像']}]), '')
        self.assertIn('慈诚罗珠堪布', template.infer_speaker('慈诚罗珠堪布：开示', []))

    def test_download_metadata_and_provenance(self):
        for human in (True, False):
            with self.subTest(human=human), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                def run(cmd, **kwargs):
                    if '--write-info-json' in cmd:
                        (root / 'id.info.json').write_text(json.dumps({
                            'title': '真实标题', 'subtitles': {'zh': [{}]} if human else {},
                            'automatic_captions': {'zh': [{}]}}))
                    elif '--skip-download' in cmd:
                        target = root / ('official' if human else 'auto') / 'id.zh.srt'
                        target.write_text('1\n00:00:00,000 --> 00:00:01,000\n字幕\n')
                    else:
                        (root / 'id.mp4').touch()
                    return SimpleNamespace(returncode=0)
                with patch.object(subtitles.subprocess, 'run', side_effect=run):
                    result = subtitles.download_url('https://youtube.com/watch?v=id', root)
                self.assertEqual(result['title'], '真实标题')
                self.assertEqual(result['track'].trust, 'official' if human else 'auto')
