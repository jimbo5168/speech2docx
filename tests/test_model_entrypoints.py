"""Cover GUI bridge methods and inference model selection with an isolated ASR_HOME."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import gui
import transcribe


class ModelEntrypointTests(unittest.TestCase):
    def test_gui_bridge_uses_one_manager(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, ASR_HOME=home):
            with patch('gui.Downloader') as factory:
                api = gui.JsApi()
                api.prepare_model_download()
                api.get_model_download()
                api.start_model_download()
                api.pause_model_download()
                factory.assert_called_once_with(Path(home))
                factory.return_value.prepare.assert_called_once_with()
                factory.return_value.status.assert_called_once_with()
                factory.return_value.start.assert_called_once_with()
                factory.return_value.pause.assert_called_once_with()

    def test_missing_r3_requests_download(self):
        with patch('transcribe.load_config', return_value={'zh_asr_model': 'buddhist'}), \
                patch('transcribe.qwen_ft_path', return_value=None), \
                patch('transcribe.resolve_hub_snapshot') as base:
            self.assertEqual(transcribe.zh_asr_choice(), 'buddhist')
            with self.assertRaisesRegex(RuntimeError, 'r3 8-bit'):
                transcribe.qwen_model_path()
            base.assert_not_called()

    def test_existing_r3_and_explicit_base_selection(self):
        with patch('transcribe.zh_asr_choice', return_value='buddhist'), \
                patch('transcribe.qwen_ft_path', return_value=Path('/test/r3')):
            self.assertEqual(transcribe.qwen_model_path(), Path('/test/r3'))
        with patch('transcribe.zh_asr_choice', return_value='base'), \
                patch('transcribe.resolve_hub_snapshot', return_value=Path('/test/base')) as base:
            self.assertEqual(transcribe.qwen_model_path(), Path('/test/base'))
            base.assert_called_once_with(transcribe.QWEN_MODEL_ID)
