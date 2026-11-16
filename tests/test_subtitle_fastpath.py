"""Generated-media integration tests. Run with python -m unittest discover -s tests -v."""
import os
import sys
import tempfile
import subprocess
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import transcribe as app
import subtitles as subs
from docx import Document

SRT = '1\n00:00:00,100 --> 00:00:00,900\n信任原字幕。\n\n2\n00:00:01,000 --> 00:00:01,800\n第二句。\n'
NOTE = '本稿直接采用原字幕（信任原字幕），未经转写与校对'


class FastPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, ASR_HOME=str(self.root / 'home')))
        self.stack.enter_context(patch.object(app, 'ensure_glossary'))
        self.stack.enter_context(patch.object(app, 'log'))
        self.stack.enter_context(patch.object(app, 'LOCK_PATH', self.root / 'lock'))
        self.media = self.root / 'sample.mp4'
        self.ff('-f', 'lavfi', '-i', 'color=s=32x32:r=1:d=2', '-f', 'lavfi', '-i',
                'anullsrc=r=16000:cl=mono', '-t', '2', '-c:v', 'mpeg4', '-c:a', 'aac', str(self.media))
        self.srt = self.root / 'input.srt'
        self.srt.write_text(SRT)

    def ff(self, *args):
        subprocess.run([subs.FFMPEG, '-nostdin', '-y', '-loglevel', 'error', *args],
                       check=True, capture_output=True, timeout=20)

    def run_fast(self, media=None, stem='result'):
        with ExitStack() as stack:
            for name in ('load_qwen', 'transcribe_qwen', 'transcribe_chunk_with_guard',
                         'plan_chunks', 'extract_audio', 'polish_text', 'proofread_chunks', 'translate_english'):
                stack.enter_context(patch.object(app, name, side_effect=AssertionError(name)))
            result = app.transcribe_file(media or self.media, lang='zh', out_dir=self.root / 'out', output_stem=stem)
        self.assertTrue(result['subtitle_fastpath'])
        doc = Path(result['docx'])
        self.assertEqual(doc.with_suffix('.srt'), Path(result['srt']))
        text = '\n'.join(p.text for p in Document(doc).paragraphs)
        self.assertIn(NOTE, text)
        self.assertIn('信任原字幕。', text)
        self.assertIn('第二句。', text)
        self.assertEqual([c.text for c in subs.parse_subtitles(Path(result['srt']))],
                         ['信任原字幕。', '第二句。'])
        return result

    def test_sidecars(self):
        for ext in ('srt', 'vtt', 'ass', 'ssa'):
            with self.subTest(ext=ext):
                side = self.media.with_suffix('.zh.' + ext)
                self.ff('-i', str(self.srt), str(side))
                self.run_fast(stem=ext)
                side.unlink()

    def test_embedded(self):
        for codec, ext in [('mov_text', 'mp4'), ('subrip', 'mkv'), ('ass', 'mkv')]:
            with self.subTest(codec=codec):
                media = self.root / f'embedded-{codec}.{ext}'
                self.ff('-i', str(self.media), '-i', str(self.srt), '-map', '0', '-map', '1',
                        '-c', 'copy', '-c:s', codec, '-metadata:s:s:0', 'language=zho', str(media))
                self.run_fast(media, codec)

    def test_no_subtitles_and_failed_extraction_fall_back(self):
        for failure in (None, RuntimeError('extract failed')):
            with self.subTest(failure=failure), ExitStack() as stack:
                if failure:
                    stack.enter_context(patch.object(app, 'find_subtitles', side_effect=failure))
                stack.enter_context(patch.object(app, 'qwen_ft_path', return_value=self.root))
                stack.enter_context(patch.object(app, 'zh_asr_choice', return_value='buddhist'))
                asr = stack.enter_context(patch.object(app, 'transcribe_chunk_with_guard', return_value='模拟识别。'))
                stack.enter_context(patch.object(app, 'polish_text', return_value={
                    'provider_used': 'none', 'paragraphs': ['模拟识别。']}))
                result = app.transcribe_file(self.media, lang='zh', out_dir=self.root / 'out')
                self.assertNotIn('subtitle_fastpath', result)
                # Remove cached chunks so each subcase exercises ASR.
                self.assertTrue(asr.called)
                for path in (self.root / 'home' / 'work').rglob('*.txt'):
                    path.unlink()

    def test_sidecar_precedence_and_existing_srt(self):
        embedded = self.root / 'preferred.mp4'
        self.ff('-i', str(self.media), '-i', str(self.srt), '-map', '0', '-map', '1',
                '-c', 'copy', '-c:s', 'mov_text', str(embedded))
        embedded.with_suffix('.srt').write_text(SRT)
        with patch.object(subs, 'extract_embedded_subtitles', side_effect=AssertionError('embedded')):
            self.run_fast(embedded)
        existing = self.root / 'out' / 'collision.srt'
        existing.write_text('keep existing subtitle')
        result = self.run_fast(embedded, 'collision')
        self.assertEqual(existing.read_text(), 'keep existing subtitle')
        self.assertNotEqual(Path(result['srt']), existing)

    def test_custom_template_and_url_track(self):
        custom = self.root / 'custom.docx'
        doc = Document()
        doc.sections[0].header.paragraphs[0].text = 'CUSTOM TEMPLATE'
        doc.save(custom)
        with patch('template.load_config', return_value={'template_path': str(custom)}):
            track = subs.SubtitleTrack(subs.parse_subtitles(self.srt), 'YouTube 官方字幕', 'official')
            with patch.object(app, 'load_qwen', side_effect=AssertionError('ASR')):
                result = app.transcribe_file(self.media, lang='auto', out_dir=self.root / 'out', subtitle_track=track)
        written = Document(result['docx'])
        self.assertEqual(written.sections[0].header.paragraphs[0].text, 'CUSTOM TEMPLATE')
        self.assertIn(NOTE, '\n'.join(p.text for p in written.paragraphs))

    def test_bitmap_codecs(self):
        for codec in ('hdmv_pgs_subtitle', 'pgs', 'dvdsub', 'dvd_subtitle', 'dvb_subtitle', 'xsub'):
            self.assertFalse(subs.is_text_subtitle_codec(codec))
        with patch.object(subs, 'list_embedded_subtitle_streams', return_value=[
            {'index': 2, 'codec_name': 'hdmv_pgs_subtitle'}]), patch.object(subs.subprocess, 'run') as run:
            self.assertIsNone(subs.extract_embedded_subtitles(self.media, self.root / 'image'))
            run.assert_not_called()

    def test_round7_remains_in_normal_chunk_path(self):
        with patch.object(app, 'to_wav16k'), patch.object(app, 'transcribe_qwen', return_value='初稿'), \
             patch.object(app, 'zh_asr_choice', return_value='buddhist'), \
             patch.object(app, 'recover_round7_omissions', return_value='重试后正文') as retry:
            result = app.transcribe_chunk_with_guard(self.media, self.root, 0, 2, 'zh', 0)
        self.assertEqual(result, '重试后正文')
        retry.assert_called_once()

    def test_roundtrip_and_timings(self):
        cues = subs.parse_subtitles(self.srt)
        dest = subs.export_cues_to_srt(cues, self.root / 'roundtrip.srt')
        self.assertEqual(cues, subs.parse_subtitles(dest))
        side = self.media.with_suffix('.srt')
        side.write_text(SRT)
        start = time.perf_counter()
        for _ in range(100):
            subs.load_sidecar(self.media)
        side_time = (time.perf_counter() - start) / 100
        side.unlink()
        embedded = self.root / 'timing.mp4'
        self.ff('-i', str(self.media), '-i', str(self.srt), '-map', '0', '-map', '1',
                '-c', 'copy', '-c:s', 'mov_text', str(embedded))
        start = time.perf_counter()
        track = subs.extract_embedded_subtitles(embedded, self.root / 'timing-subs')
        embedded_time = time.perf_counter() - start
        self.assertEqual(cues, track.cues)
        result = self.run_fast(embedded)
        print(f'TIMING sidecar_mean_seconds={side_time:.8f} embedded_seconds={embedded_time:.6f} '
              f'full_docx_srt_seconds={result["elapsed"]:.6f}', flush=True)


if __name__ == '__main__':
    unittest.main()
