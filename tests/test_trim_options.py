"""Retired options cannot be restored by old settings or stale UI clients."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import config
import gui
import transcribe
from docx import Document


class TrimOptionsTests(unittest.TestCase):
    def test_legacy_settings_migrate_on_disk_and_stale_saves_stay_disabled(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, ASR_HOME=d):
            path = Path(d) / 'config.json'
            path.write_text(json.dumps({'english_output': 'bilingual', 'include_raw_appendix': True}))
            cfg = config.load_config()
            self.assertEqual(cfg['english_output'], 'zh')
            self.assertFalse(cfg['include_raw_appendix'])
            self.assertEqual(json.loads(path.read_text())['english_output'], 'zh')
            with patch.object(gui.AppState, 'snapshot', return_value={}):
                gui.JsApi().save_settings({'english_output': 'bilingual', 'include_raw_appendix': True})
            self.assertFalse(json.loads(path.read_text())['include_raw_appendix'])
            config.save_config({'english_output': 'bilingual', 'include_raw_appendix': True})
            self.assertEqual(json.loads(path.read_text())['english_output'], 'zh')

    def test_ui_only_accepts_chinese(self):
        state = gui.AppState()
        with patch.object(gui, 'STATE', state), patch.object(gui.AppState, 'snapshot', return_value={}):
            for lang in ('en', 'bo', 'auto'):
                gui.JsApi().set_lang(lang)
                self.assertEqual(state.lang, 'zh')
        html = (Path(__file__).resolve().parents[1] / 'ui/index.html').read_text()
        for retired in ('opt-appendix', 'english-group', 'value="bo"', 'value="en"'):
            self.assertNotIn(retired, html)

    def test_english_rejected_before_processing(self):
        with patch.object(transcribe, 'translate_english', side_effect=AssertionError('translation')):
            with self.assertRaisesRegex(ValueError, '不支持英文转中文'):
                transcribe.transcribe_file(Path('english.mp3'), lang='en')

    def test_both_word_writers_ignore_legacy_appendix_and_preserve_mantra(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, ASR_HOME=d):
            kwargs = dict(title='讲课', source_name='lecture.mp3', sections=[{'paragraphs': ['中文咒语 ཨོཾ།']}],
                          summary='', note='', lang='zh', duration=1, polish_label='', raw_appendix='不可出现的原始稿')
            for fallback in (False, True):
                with patch.object(transcribe, 'write_from_template', side_effect=RuntimeError('fallback')) if fallback else patch.object(transcribe, 'log'):
                    dest = transcribe.write_docx(Path(d) / f'{fallback}.docx', **kwargs)
                text = '\n'.join(p.text for p in Document(dest).paragraphs)
                self.assertNotIn('附录', text)
                self.assertNotIn('不可出现', text)
                self.assertIn('ཨོཾ།', text)
                self.assertIn('听打稿', text)
                self.assertIn('来源', text)
