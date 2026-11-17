"""Local recognition and Word output must not depend on a polish service."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import config
import polish
from test_subtitle_fastpath import FastPathTests
from docx import Document


class NoLMStudioTests(unittest.TestCase):
    def test_defaults_and_legacy_local_require_opt_in(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, ASR_HOME=d):
            self.assertEqual(config.load_config()['polish_provider'], 'none')
            config.save_config({'polish_provider': 'local'})
            self.assertEqual(config.load_config()['polish_provider'], 'none')
            config.save_config({'polish_provider': 'local', 'local_polish_opt_in': True})
            self.assertEqual(config.load_config()['polish_provider'], 'local')

    def test_default_never_calls_service(self):
        with patch.object(polish, 'load_config', return_value=config.DEFAULTS), patch.object(polish.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            self.assertEqual(polish.polish_text('原文。')['provider_used'], 'none')
            self.assertEqual(polish.translate_english(['Hello.'])['provider_used'], 'none')

    def test_probe_requires_loaded_matching_qwen(self):
        for models, expected in [([], False), ([{'id': config.DEFAULTS['models']['local'], 'state': 'not-loaded'}], False), ([{'id': 'other-qwen', 'state': 'loaded'}], False), ([{'id': config.DEFAULTS['models']['local'], 'state': 'loaded'}], True)]:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps({'data': models}).encode()
            with patch.object(polish.urllib.request, 'urlopen', return_value=response) as request:
                self.assertEqual(polish.local_qwen_available(config.DEFAULTS), expected)
                self.assertEqual(request.call_args.kwargs['timeout'], .35)

    def test_settings_reject_unavailable_polish(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, ASR_HOME=d):
            import gui
            state = gui.AppState()
            with patch.object(gui, 'STATE', state), patch.object(gui.AppState, 'snapshot', return_value={}):
                gui.JsApi().save_settings({'polish_provider': 'local'})
                self.assertEqual(config.load_config()['polish_provider'], 'none')
                state.local_polish_available = True
                gui.JsApi().save_settings({'polish_provider': 'local'})
                self.assertEqual(config.load_config()['polish_provider'], 'local')

    def test_unreachable_selected_polish_keeps_original(self):
        with patch.object(polish, 'load_config', return_value=config.DEFAULTS), patch.object(polish.urllib.request, 'urlopen', side_effect=ConnectionRefusedError), patch.object(polish.subprocess, 'run', side_effect=AssertionError('must not start server')):
            self.assertFalse(polish.local_qwen_available(config.DEFAULTS))
            result = polish.polish_text('原文。', provider='local')
            self.assertEqual(result['paragraphs'], ['原文。'])
            self.assertEqual(result['provider_used'], 'none')
            self.assertIn('保留', result['note'])


class NoServiceTranscriptionTests(unittest.TestCase):
    setUp = FastPathTests.setUp
    ff = FastPathTests.ff


def test_transcription_without_service(self):
    import transcribe as app
    with patch.object(app, 'qwen_ft_path', return_value=self.root), patch.object(app, 'zh_asr_choice', return_value='buddhist'), patch.object(app, 'transcribe_chunk_with_guard', return_value='本地识别正文。'), patch.object(polish.urllib.request, 'urlopen', side_effect=AssertionError('no service needed')):
        result = app.transcribe_file(self.media, lang='zh', out_dir=self.root / 'out')
    self.assertEqual(result['provider_used'], 'none')
    self.assertIn('本地识别正文。', '\n'.join(p.text for p in Document(result['docx']).paragraphs))

NoServiceTranscriptionTests.test_transcription_without_service = test_transcription_without_service
# Avoid discovering the imported base fixture a second time.
del FastPathTests
