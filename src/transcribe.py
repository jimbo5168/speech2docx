#!/usr/bin/env python3
"""Transcribe audio to a polished Word document in ~/Downloads."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
from difflib import SequenceMatcher
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# Allow running from $ASR_HOME or from the repo src/ directory
_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config import load_config
from zh_normalization import canonicalize_variants
from glossary import as_hotwords, as_prompt_block, ensure_glossary
from notify import notify
from media import MEDIA_EXTS, extract_audio, is_video, media_duration
from subtitles import (
    download_url,
    export_cues_to_srt,
    find_subtitles,
    format_proofread_note,
    is_media_url,
    proofread_chunks,
    SubtitleTrack,
)
from paths import (
    FFMPEG,
    FFPROBE,
    asr_home,
    default_out_dir,
    jobs_dir,
    logs_dir,
    raw_dir,
    work_dir,
)
from polish import make_summary, polish_text, protect_review_markers, restore_review_markers, translate_english
from template import write_from_template

TZ = ZoneInfo("America/Los_Angeles")
LOCK_PATH = logs_dir() / "transcribe.lock"
QWEN_MODEL_ID = "mlx-community/Qwen3-ASR-1.7B-8bit"
# Buddhist-lecture LoRA fine-tune (merged, 8-bit), built by scripts/finetune/.
QWEN_FT_DIRNAME = "qwen3-asr-1.7b-buddhist-8bit"
TIBETAN_MODEL_ID = "tenzin/whisper-small-tibetan-ct2"
AUDIO_EXTS = MEDIA_EXTS  # audio + video; video soundtrack is extracted first
LANG_TO_QWEN = {
    "zh": "Chinese", "yue": "Cantonese", "en": "English", "ja": "Japanese",
    "ko": "Korean", "de": "German", "es": "Spanish", "fr": "French",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian",
}

_qwen_model = None
_tibetan_model = None
_opencc = None


def log(msg: str) -> None:
    line = f"{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')} {msg}"
    with (logs_dir() / "transcribe.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def resolve_hub_snapshot(repo_id: str) -> Path:
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    dirname = "models--" + repo_id.replace("/", "--")
    ref = cache / dirname / "refs" / "main"
    if ref.exists():
        snap = cache / dirname / "snapshots" / ref.read_text().strip()
        if snap.exists():
            return snap
    snaps = cache / dirname / "snapshots"
    if snaps.exists():
        cands = [p for p in snaps.iterdir() if p.is_dir()]
        if cands:
            return sorted(cands, key=lambda p: p.stat().st_mtime)[-1]
    return Path(repo_id)


def audio_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            stderr=subprocess.STDOUT, text=True,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def to_wav16k(src: Path, dst: Path, start: Optional[float] = None, duration: Optional[float] = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg failed converting {src.name}")
    return dst


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    cand = path.with_name(f"{path.stem}-{stamp}{path.suffix}")
    i = 2
    while cand.exists():
        cand = path.with_name(f"{path.stem}-{stamp}-{i}{path.suffix}")
        i += 1
    return cand


def to_simplified(text: str) -> str:
    global _opencc
    if not text:
        return text
    try:
        if _opencc is None:
            from opencc import OpenCC
            _opencc = OpenCC("t2s")
        protected, held = protect_review_markers(text)
        simplified = _opencc.convert(protected)
        if load_config().get("zh_variant_normalization", True):
            simplified = canonicalize_variants(simplified)
        return restore_review_markers(simplified, held)
    except Exception:
        return text


def qwen_ft_path() -> Optional[Path]:
    from paths import asr_home
    p = Path(asr_home()) / "models" / QWEN_FT_DIRNAME
    return p if (p / "model.safetensors").exists() else None


def zh_asr_choice() -> str:
    """'buddhist' (fine-tuned, default) or 'base'. Falls back to base if missing."""
    try:
        from config import load_config
        choice = load_config().get("zh_asr_model", "buddhist")
    except Exception:
        choice = "buddhist"
    if choice == "buddhist" and qwen_ft_path() is None:
        return "base"
    return "base" if choice == "base" else "buddhist"


def load_qwen():
    global _qwen_model
    if _qwen_model is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from mlx_audio.stt import load
        path = qwen_ft_path() if zh_asr_choice() == "buddhist" else None
        path = path or resolve_hub_snapshot(QWEN_MODEL_ID)
        log(f"Loading Qwen3-ASR from {path}")
        t0 = time.perf_counter()
        _qwen_model = load(str(path))
        log(f"Qwen3-ASR loaded in {time.perf_counter() - t0:.1f}s")
    return _qwen_model


def load_tibetan():
    global _tibetan_model
    if _tibetan_model is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from faster_whisper import WhisperModel
        path = resolve_hub_snapshot(TIBETAN_MODEL_ID)
        log(f"Loading Tibetan Whisper from {path}")
        t0 = time.perf_counter()
        _tibetan_model = WhisperModel(str(path), device="cpu", compute_type="int8")
        log(f"Tibetan Whisper loaded in {time.perf_counter() - t0:.1f}s")
    return _tibetan_model


ASR_PRIORITY_TERMS = [
    "论典", "不堕两边", "出离心", "菩提心", "顺缘", "违缘",
    "因明", "依止五火", "阿赖耶识", "五部大论", "善根", "根机",
    "殊胜", "常见", "断见", "苦行", "精进", "五欲", "少欲知足",
    "亲友书", "慈诚罗珠堪布", "米拉日巴", "龙树菩萨", "大圆满", "堪布",
]


def asr_model_label(lang: str) -> str:
    if lang == "bo":
        return "Tibetan Whisper small（tenzin/whisper-small-tibetan-ct2，本地）"
    if zh_asr_choice() == "buddhist":
        return "Qwen3-ASR 1.7B 佛学微调版（8-bit，本地）"
    return "Qwen3-ASR 1.7B-8bit（mlx-community，本地）"


def _qwen_bias() -> tuple[list[str], str]:
    # Round 4: the legacy long prompt regressed held-out and both sanity samples.
    # Keep it opt-in, with the existing editable glossary, rather than silently
    # biasing every decode. No prompt is the measured default for the r3 model.
    if not load_config().get("zh_asr_context_enabled", False):
        return [], ""
    terms: list[str] = []
    seen: set[str] = set()
    for term in ASR_PRIORITY_TERMS + as_hotwords(400):
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    block = "、".join(terms)
    if len(block) > 4000:
        block = block[:4000].rsplit("、", 1)[0]
    prompt = (
        "请准确识别这段藏传佛教讲课中的专有名词与术语，按词表用字，"
        "不要把「论典」听成「论点」，不要把「出离心」听成「处理心」，"
        "不要把「顺缘」「违缘」听成「损缘」「微缘」，"
        "不要把「不堕两边」听成「不多两边」，"
        "不要把「因明」「依止五火」听成「遗民」「一枝乌火」。词表："
        + block
    )
    return terms, prompt


def find_runaway_repeat(text: str, min_times: int = 6) -> tuple[str, int] | None:
    """Return (phrase, count) if a 2–40 character phrase repeats consecutively."""
    if not text or len(text) < 12:
        return None
    import re
    pat = re.compile(r"(.{2,40}?)\1{" + str(min_times - 1) + r",}")
    match = pat.search(text)
    if not match:
        return None
    phrase = match.group(1)
    count = 0
    idx = match.start()
    while text.startswith(phrase, idx):
        count += 1
        idx += len(phrase)
    return phrase, count


def collapse_runaway_repeats(text: str, min_times: int = 6) -> tuple[str, int]:
    """Keep one copy of each runaway consecutive repeat. Returns (text, collapsed_runs)."""
    if not text:
        return text, 0
    import re
    runs = 0
    pat = re.compile(r"(.{2,40}?)(\1){" + str(min_times - 1) + r",}")

    def _sub(match: "re.Match") -> str:
        nonlocal runs
        runs += 1
        return match.group(1)

    # Repeat until stable; a long file may have more than one site.
    out = text
    for _ in range(8):
        nxt, n = pat.subn(_sub, out)
        if n == 0:
            break
        out = nxt
    return out, runs


def qwen_repetition_penalty(lang: Optional[str]) -> float:
    """Use the measured Chinese default while preserving other language defaults."""
    if lang not in ("zh", "Chinese"):
        return 1.15
    try:
        penalty = float(load_config().get("zh_asr_repetition_penalty", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return penalty if math.isfinite(penalty) and penalty > 0.0 else 1.0


def transcribe_qwen(
    wav: Path,
    lang: Optional[str],
    repetition_penalty: Optional[float] = None,
    temperature: float = 0.0,
) -> str:
    if repetition_penalty is None:
        repetition_penalty = qwen_repetition_penalty(lang)
    model = load_qwen()
    hotwords, system_prompt = _qwen_bias()
    kwargs = {
        "verbose": False,
        "hotwords": hotwords,
        "system_prompt": system_prompt,
        "repetition_penalty": repetition_penalty,
        "temperature": temperature,
    }
    if lang and lang not in ("auto", "bo"):
        qwen_lang = LANG_TO_QWEN.get(lang, lang)
        if str(qwen_lang).lower() == "zh":
            qwen_lang = "Chinese"
        kwargs["language"] = qwen_lang
    result = model.generate(str(wav), **kwargs)
    if isinstance(result, list):
        result = result[0]
    text = (getattr(result, "text", None) or "").strip()
    if not text:
        segs = getattr(result, "segments", None) or []
        parts = []
        for s in segs:
            parts.append(s.get("text") if isinstance(s, dict) else getattr(s, "text", ""))
        text = "".join(p or "" for p in parts).strip()
    return to_simplified(text)


# Round 7, approved 2026-11-16. See ~/ASR/codex_round7.md and
# ~/ASR/train/round7/install_gate.json. r3 weights are retained.
_ROUND7_SKIP_LINE_RE = re.compile('^(摘要|听打稿|聽打稿|来源：|來源：|校对说明|校對說明|附录[:：]|附錄[:：]|识别：|識別：|修饰：|修飾：|说明：|說明：|转写时间|轉寫時間)')

_ROUND7_PUNCT_RE = re.compile('[\\s\\u3000-\\u303f\\uff00-\\uff0f\\uff1a-\\uff20\\uff3b-\\uff40\\uff5b-\\uff65\\u2000-\\u206f\\u2e00-\\u2e7f!\\"#$%&\'()*+,\\-./:;<=>?@\\[\\\\\\]^_`{|}~，。！？、；：「」『』（）【】《》—…·]+')

def _round7_body_text(text: str) -> str:
    lines = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line:
            continue
        if _ROUND7_SKIP_LINE_RE.match(line):
            continue
        if line.startswith('以下为识别模型的原文'):
            break
        lines.append(line)
    return '\n'.join(lines)

def _round7_normalize(text: str) -> str:
    return _ROUND7_PUNCT_RE.sub('', _round7_body_text(text))

def _round7_repeat_spans(s):
    spans = []
    for size in range(1, 25):
        i = 0
        while i + size * 2 <= len(s):
            unit = s[i:i + size]
            n = 1
            while s[i + n * size:i + (n + 1) * size] == unit:
                n += 1
            if n >= 2:
                spans.append((i, i + n * size, size, n, unit))
                i += n * size
            else:
                i += 1
    return spans

def _round7_select(old, retry):
    a, b = (_round7_normalize(old), _round7_normalize(retry))
    if not a or not 1 <= len(b) / len(a) <= 1.15:
        return (old, [])
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    adds = []
    for k, (tag, i, j, u, v) in enumerate(ops):
        if tag != 'insert' or not 1 <= v - u <= 8 or k == 0 or (k == len(ops) - 1):
            continue
        prev, nex = (ops[k - 1], ops[k + 1])
        if prev[0] != 'equal' or nex[0] != 'equal' or prev[2] - prev[1] < 6 or (nex[2] - nex[1] < 6):
            continue
        if _round7_repeat_spans(b[max(0, u - 8):min(len(b), v + 8)]):
            continue
        pos = next((p for p in range(len(old) + 1) if len(_round7_normalize(old[:p])) == i), None)
        if pos is not None:
            adds.append((pos, b[u:v]))
    result = old
    for pos, txt in reversed(adds):
        result = result[:pos] + txt + result[pos:]
    assert all((tag in ('equal', 'insert') for tag, *_ in SequenceMatcher(None, old, result, autojunk=False).get_opcodes()))
    return (result, adds)


def recover_round7_omissions(wav: Path, wav_dir: Path, duration: float, text: str, index: int) -> str:
    """Apply the frozen reference-free, insertion-only Round 7 retry rule."""
    if duration <= 0:
        return text
    cps = len(_round7_normalize(text)) / duration
    if cps > 2.3295141722123502 and duration < 27.51499999999976:
        return text
    try:
        from opencc import OpenCC
        converter = OpenCC('t2s').convert
        model = load_qwen()
        parts = []
        for part in range(2):
            if shutil.disk_usage(asr_home()).free < 50 * 1024 ** 3:
                raise RuntimeError('Round7 disk below 50 GiB')
            while True:
                pressure = subprocess.check_output(['memory_pressure'], text=True)
                free = int(re.search(r'free percentage: (\d+)', pressure)[1])
                if free >= 15:
                    break
                log(f'Round7 waiting for memory, free={free}%')
                time.sleep(60)
            half = wav_dir / f'{index:03d}-round7-{part}.wav'
            try:
                subprocess.run([FFMPEG, '-nostdin', '-y', '-loglevel', 'error',
                                '-ss', str(duration * part / 2), '-i', str(wav),
                                '-t', str(duration / 2), '-ar', '16000', '-ac', '1', str(half)], check=True)
                result = model.generate(str(half), language='Chinese', repetition_penalty=1.0,
                                        max_tokens=int(duration / 2 * 10) + 40, verbose=False)
                parts.append(converter(result.text.strip()))
            finally:
                half.unlink(missing_ok=True)
        recovered, additions = _round7_select(text, ''.join(parts))
        if additions:
            log(f'Round7 inserted {sum(len(s) for _, s in additions)} chars in chunk {index + 1}')
        return recovered
    except Exception as error:
        log(f'Round7 retry failed, preserving initial output: {error}')
        return text


def transcribe_chunk_with_guard(
    media_src: Path,
    wav_dir: Path,
    start: float,
    dur: float,
    lang: str,
    index: int,
) -> str:
    """Transcribe one planned chunk; if the ASR loops, retry in shorter slices."""
    wav = wav_dir / f"{index:03d}.wav"
    to_wav16k(media_src, wav, start=start, duration=dur)
    try:
        if lang == "bo":
            text = transcribe_tibetan(wav)
        else:
            text = transcribe_qwen(wav, lang)
        if lang == "zh" and zh_asr_choice() == "buddhist":
            text = recover_round7_omissions(wav, wav_dir, dur, text, index)
        found = find_runaway_repeat(text)
        if not found:
            return text
        phrase, times = found
        log(f"REPEAT in chunk {index+1}: {phrase!r} x{times}; retrying in 60s slices")
        parts: list[str] = []
        for j, (sub_start, sub_dur) in enumerate(plan_chunks(dur, 60.0, 3.0)):
            sub = wav_dir / f"{index:03d}-{j:02d}.wav"
            to_wav16k(media_src, sub, start=start + sub_start, duration=sub_dur)
            try:
                if lang == "bo":
                    piece = transcribe_tibetan(sub)
                else:
                    piece = transcribe_qwen(sub, lang, repetition_penalty=1.35)
            finally:
                try:
                    sub.unlink()
                except OSError:
                    pass
            piece, collapsed = collapse_runaway_repeats(piece)
            if collapsed:
                log(f"REPEAT collapsed in slice {index+1}.{j+1} runs={collapsed}")
            parts.append(piece)
        joined = "".join(parts)
        joined, collapsed = collapse_runaway_repeats(joined)
        if collapsed:
            log(f"REPEAT collapsed after retry in chunk {index+1} runs={collapsed}")
        return joined
    finally:
        try:
            wav.unlink()
        except OSError:
            pass


def load_wav_mono_f32(path: Path):
    import wave
    import numpy as np

    with wave.open(str(path), "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise RuntimeError(f"unexpected sample width {sw}")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        audio = audio.reshape(-1, nch).mean(axis=1)
    return audio


def transcribe_tibetan(wav: Path) -> str:
    model = load_tibetan()
    audio = load_wav_mono_f32(wav)
    prompt = "、".join(as_hotwords(80))
    segs, _info = model.transcribe(
        audio,
        language="bo",
        beam_size=5,
        vad_filter=False,
        without_timestamps=False,
        initial_prompt=prompt[:800] if prompt else None,
    )
    parts = [(s.text or "").strip() for s in segs if (s.text or "").strip()]
    return " ".join(parts).strip()


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = open(self.path, "a")
        fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
        finally:
            self.fd.close()
            self.fd = None


def detect_lang(src: Path, requested: str) -> str:
    lang = (requested or "auto").lower()
    if lang in ("zh-cn", "zh_cn", "cn", "chinese", "cmn"):
        return "zh"
    if lang in ("en", "eng", "english"):
        return "en"
    if lang in ("tibetan", "tib", "bo-cn"):
        return "bo"
    if lang == "auto":
        hay = str(src).lower() + " " + src.name
        if "藏" in src.name or "藏" in str(src) or "tibet" in hay or hay.endswith(".bo"):
            return "bo"
        if "英文" in src.name or "英语" in src.name or "english" in hay or "_en." in hay or "-en." in hay or hay.endswith(".en"):
            return "en"
        return "auto"
    return lang


def infer_script(text: str) -> str:
    """Guess zh / en / bo from already recognized text."""
    if not text or not text.strip():
        return "zh"
    bo = sum(1 for ch in text if "\u0f00" <= ch <= "\u0fff")
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if bo > 20 and bo >= cjk and bo * 2 >= latin:
        return "bo"
    if latin >= 40 and latin > cjk * 2:
        return "en"
    return "zh"


def job_id_for(src: Path) -> str:
    st = src.stat()
    raw = f"{src.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    if zh_asr_choice() == "buddhist":  # don't reuse chunks recognised by another ASR model
        raw += "|asr=buddhist"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def plan_chunks(duration: float, chunk: float, overlap: float) -> list[tuple[float, float]]:
    if duration <= 0:
        return [(0.0, 0.0)]
    if duration <= chunk + 30:
        return [(0.0, duration)]
    step = max(chunk - overlap, chunk * 0.8)
    out = []
    start = 0.0
    while start < duration - 0.5:
        end = min(duration, start + chunk)
        out.append((start, end - start))
        if end >= duration:
            break
        start += step
    return out


def set_run_font(run, lang: str, size=12) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    font_name = "Kailasa" if lang == "bo" else "PingFang SC"
    run.font.size = Pt(size)
    run.font.name = font_name
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:eastAsia"), font_name)
    rFonts.set(qn("w:cs"), font_name)



def emit_progress(on_progress, stage: str, percent=None, message: str = "") -> None:
    if not on_progress:
        return
    try:
        on_progress(stage, percent, message)
    except Exception:
        pass


def format_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h} 小时 {m} 分" if sec == 0 else f"{h} 小时 {m} 分 {sec} 秒"
    if m:
        return f"{m} 分钟" if sec == 0 else f"{m} 分 {sec} 秒"
    return f"{sec} 秒"



def _highlight_spans(text: str, phrases: list[str]) -> list[tuple[str, bool]]:
    """Split text into (fragment, highlighted) runs."""
    if not text or not phrases:
        return [(text, False)]
    marks = [False] * len(text)
    for ph in sorted({p for p in phrases if p}, key=len, reverse=True):
        start = 0
        while True:
            i = text.find(ph, start)
            if i < 0:
                break
            for k in range(i, i + len(ph)):
                marks[k] = True
            start = i + max(len(ph), 1)
    runs: list[tuple[str, bool]] = []
    buf = []
    flag = marks[0]
    for ch, hl in zip(text, marks):
        if hl != flag and buf:
            runs.append(("".join(buf), flag))
            buf = [ch]
            flag = hl
        else:
            buf.append(ch)
    if buf:
        runs.append(("".join(buf), flag))
    return runs


def add_body_paragraph(doc, text: str, lang: str, highlight_phrases: list[str] | None = None, size: int = 12, indent: bool = True):
    from docx.shared import Cm, Pt, RGBColor
    p = doc.add_paragraph()
    text = str(text).strip()
    for frag, hl in _highlight_spans(text, highlight_phrases or []):
        r = p.add_run(frag)
        set_run_font(r, lang, size)
        if hl:
            r.font.highlight_color = 7  # wdYellow
            try:
                r.font.highlight_color = 7
            except Exception:
                pass
            # python-docx uses WD_COLOR_INDEX
            try:
                from docx.enum.text import WD_COLOR_INDEX
                r.font.highlight_color = WD_COLOR_INDEX.YELLOW
            except Exception:
                pass
    p.paragraph_format.space_after = Pt(10)
    p.paragraph_format.line_spacing = 1.35
    if indent:
        p.paragraph_format.first_line_indent = Cm(0.74)
    return p



def write_docx(
    dest: Path,
    title: str,
    source_name: str,
    sections: list[dict],
    summary: str,
    note: str,
    lang: str,
    duration: float,
    polish_label: str,
    asr_label: str = "",
    raw_appendix: str | list[str] | None = None,
    overwrite: bool = False,
    proofread_note: str = "",
    highlight_phrases: list[str] | None = None,
    speaker: str = "",
    title_zh: str = "",
    bilingual_pairs: list[dict] | None = None,
) -> Path:
    if not overwrite:
        dest = unique_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        return write_from_template(
            dest,
            title=title,
            source_name=source_name,
            sections=sections,
            summary=summary,
            note=note,
            lang=lang,
            duration_label=format_duration(duration),
            polish_label=polish_label,
            asr_label=asr_label,
            raw_appendix=raw_appendix,
            proofread_note=proofread_note,
            highlight_phrases=highlight_phrases,
            speaker=speaker,
            title_zh=title_zh,
            bilingual_pairs=bilingual_pairs,
        )
    except Exception as exc:
        log(f"TEMPLATE fallback: {exc}")
        return _write_docx_plain(
            dest,
            title,
            source_name,
            sections,
            summary,
            note,
            lang,
            duration,
            polish_label,
            asr_label=asr_label,
            raw_appendix=raw_appendix,
            overwrite=True,
            proofread_note=proofread_note,
            highlight_phrases=highlight_phrases,
        )


def _write_docx_plain(
    dest: Path,
    title: str,
    source_name: str,
    sections: list[dict],
    summary: str,
    note: str,
    lang: str,
    duration: float,
    polish_label: str,
    asr_label: str = "",
    raw_appendix: str | list[str] | None = None,
    overwrite: bool = False,
    proofread_note: str = "",
    highlight_phrases: list[str] | None = None,
) -> Path:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    if not overwrite:
        dest = unique_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.54)
        section.right_margin = Cm(2.54)

    font_name = "Kailasa" if lang == "bo" else "PingFang SC"
    style = doc.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(12)
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), font_name)
    rFonts.set(qn("w:hAnsi"), font_name)
    rFonts.set(qn("w:eastAsia"), font_name)
    rFonts.set(qn("w:cs"), font_name)

    h = doc.add_heading(title, level=1)
    for run in h.runs:
        set_run_font(run, lang, 18)

    when = datetime.now(TZ).strftime("%Y-%m-%d %H:%M %Z")
    meta = doc.add_paragraph()
    asr_bit = f"    识别：{asr_label}" if asr_label else ""
    r = meta.add_run(f"来源：{source_name}    时长：{format_duration(duration)}    转写时间：{when}{asr_bit}    修饰：{polish_label}")
    set_run_font(r, "zh", 10)
    r.italic = True
    r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    if note:
        p = doc.add_paragraph()
        r = p.add_run("说明：" + note)
        set_run_font(r, "zh", 10)
        r.font.color.rgb = RGBColor(0x88, 0x44, 0x00)

    if proofread_note:
        ph = doc.add_heading("校对说明", level=2)
        for run in ph.runs:
            set_run_font(run, "zh", 14)
        for line in str(proofread_note).splitlines():
            if not line.strip():
                continue
            p = doc.add_paragraph()
            r = p.add_run(line.strip())
            set_run_font(r, "zh", 11)
            p.paragraph_format.space_after = Pt(4)

    if summary:
        sh = doc.add_heading("摘要", level=2)
        for run in sh.runs:
            set_run_font(run, "zh", 14)
        p = doc.add_paragraph()
        r = p.add_run(summary)
        set_run_font(r, "zh", 12)
        p.paragraph_format.space_after = Pt(12)

    for sec in sections:
        heading = (sec.get("heading") or "").strip()
        if heading:
            hh = doc.add_heading(heading, level=2)
            for run in hh.runs:
                set_run_font(run, lang, 14)
        for para in sec.get("paragraphs") or []:
            if not str(para).strip():
                continue
            add_body_paragraph(doc, str(para), lang, highlight_phrases)

    if not any(sec.get("paragraphs") for sec in sections):
        p = doc.add_paragraph()
        r = p.add_run("（未识别到语音内容）")
        set_run_font(r, "zh", 12)

    appendix_parts: list[str] = []
    if isinstance(raw_appendix, list):
        appendix_parts = [str(x).strip() for x in raw_appendix if str(x).strip()]
    elif isinstance(raw_appendix, str) and raw_appendix.strip():
        appendix_parts = [p.strip() for p in raw_appendix.split("\n") if p.strip()] or [raw_appendix.strip()]
    if appendix_parts:
        ah = doc.add_heading("附录：原始转录", level=2)
        for run in ah.runs:
            set_run_font(run, "zh", 14)
        hint = doc.add_paragraph()
        r = hint.add_run("以下为识别模型的原文，未经修饰，便于对照准确度。")
        set_run_font(r, "zh", 10)
        r.italic = True
        r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        for part in appendix_parts:
            p = doc.add_paragraph()
            r = p.add_run(part)
            set_run_font(r, lang, 11)
            p.paragraph_format.space_after = Pt(10)
            p.paragraph_format.line_spacing = 1.35

    doc.save(str(dest))
    return dest


def transcribe_file(
    src: Path,
    lang: str = "auto",
    out_dir: Optional[Path] = None,
    output_stem: Optional[str] = None,
    polish: Optional[str] = None,
    do_notify: bool = False,
    open_docx: bool = False,
    sidecar_dirs: Optional[list[Path]] = None,
    subtitle_track: Optional[SubtitleTrack] = None,
    on_progress=None,
) -> dict:
    ensure_glossary()
    src = src.expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"找不到文件：{src}")
    if src.suffix.lower() not in MEDIA_EXTS:
        raise ValueError(f"不支持的音频或视频格式：{src.suffix}")

    out_dir = Path(out_dir).expanduser() if out_dir else default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem or src.stem
    source_name = (stem + src.suffix) if output_stem else src.name
    lang = detect_lang(src, lang)
    cfg = load_config()
    chunk_s = float(cfg.get("chunk_seconds") or 480)
    overlap_s = float(cfg.get("overlap_seconds") or 4)
    jid = job_id_for(src)
    job = jobs_dir() / jid
    # Resolve text subtitles before audio extraction, model lookup, or chunking.
    fast_started = time.perf_counter()
    extras = list(sidecar_dirs or []) + [src.parent]
    try:
        track = find_subtitles(src, job, extra_dirs=extras, prefer="zh")
    except Exception as exc:
        log(f"SUBTITLE lookup failed, using ASR: {exc}")
        track = None
    track = track or subtitle_track
    if track and track.cues:
        emit_progress(on_progress, "write", 96, "直接采用原字幕，正在生成文档…")
        note = "本稿直接采用原字幕（信任原字幕），未经转写与校对"
        sections = [{"heading": "", "paragraphs": [c.text for c in track.cues]}]
        duration = max(c.end for c in track.cues)
        dest = unique_path(out_dir / f"{stem}.docx")
        while dest.with_suffix(".srt").exists():
            dest = unique_path(dest.with_name(dest.stem + "-字幕.docx"))
        docx_path = write_docx(
            dest, title=stem, source_name=source_name,
            sections=sections, summary="", note=note,
            lang="bo" if lang == "bo" else "zh", duration=duration,
            polish_label="未经校对", asr_label="原字幕直出",
        )
        srt_path = export_cues_to_srt(track.cues, docx_path.with_suffix(".srt"))
        elapsed = time.perf_counter() - fast_started
        log(f"SUBTITLE FAST PATH source={track.source} cues={len(track.cues)} "
            f"elapsed={elapsed:.4f}s -> {docx_path.name}, {srt_path.name}")
        emit_progress(on_progress, "done", 100, "完成（信任原字幕）")
        if do_notify:
            notify("为我闻思", f"已直接采用原字幕生成「{docx_path.name}」")
        if open_docx:
            subprocess.run(["/usr/bin/open", str(docx_path)], check=False)
        return {
            "source": str(src), "docx": str(docx_path), "srt": str(srt_path),
            "raw": "", "text": "\n".join(c.text for c in track.cues),
            "polished_sections": sections, "summary": "", "note": note,
            "duration": duration, "elapsed": elapsed,
            "rtf_x": duration / elapsed if elapsed else 0.0,
            "lang": lang, "provider_used": "none", "subtitle_fastpath": True,
        }
    raw_chunks = job / "raw"
    if lang == "zh":
        model_path = qwen_ft_path() if zh_asr_choice() == "buddhist" else None
        model_path = model_path or resolve_hub_snapshot(QWEN_MODEL_ID)
        weights = [
            (p.name, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in sorted(model_path.glob("*.safetensors"))
        ]
        profile = json.dumps([qwen_repetition_penalty(lang), str(model_path), weights])
        profile_id = hashlib.sha256(profile.encode()).hexdigest()[:16]
        raw_chunks = job / f"raw-r5-{profile_id}"
    wav_dir = job / "wav"
    raw_chunks.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)

    media_src = src
    if is_video(src):
        extracted = job / "source.wav"
        if not extracted.exists() or extracted.stat().st_size == 0:
            log(f"EXTRACT audio from video {src.name}")
            emit_progress(on_progress, "extract", 3, "正在抽取音轨…")
            extract_audio(src, extracted)
        media_src = extracted
        log(f"VIDEO soundtrack ready: {extracted.name}")

    duration = audio_duration(media_src) or media_duration(src)
    if do_notify:
        notify("为我闻思", f"开始转写「{source_name}」（约 {int(duration//60)} 分钟）")

    log(f"START {src.name} lang={lang} duration={duration:.1f}s")
    emit_progress(on_progress, "transcribe", 5, "开始识别…")
    t0 = time.perf_counter()

    plan = plan_chunks(duration, chunk_s, overlap_s)
    (job / "meta.json").write_text(
        json.dumps({"source": str(src), "duration": duration, "chunks": len(plan), "lang": lang}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    texts: list[str] = []
    with FileLock(LOCK_PATH):
        for i, (start, dur) in enumerate(plan):
            raw_path = raw_chunks / f"{i:03d}.txt"
            cached = ""
            if raw_path.exists() and raw_path.stat().st_size > 0:
                cached = raw_path.read_text(encoding="utf-8")
            if cached and not find_runaway_repeat(cached):
                texts.append(cached)
                log(f"RESUME chunk {i+1}/{len(plan)}")
                pct = 5 + int(70 * (i + 1) / max(len(plan), 1))
                emit_progress(on_progress, "transcribe", pct, f"沿用已识别片段 {i+1}/{len(plan)}")
                continue
            if cached and find_runaway_repeat(cached):
                log(f"REPEAT in cached chunk {i+1}/{len(plan)}; re-running")
            text = transcribe_chunk_with_guard(media_src, wav_dir, start, dur, lang, i)
            raw_path.write_text(text, encoding="utf-8")
            texts.append(text)
            log(f"ASR chunk {i+1}/{len(plan)} start={start:.0f}s chars={len(text)}")
            pct = 5 + int(70 * (i + 1) / max(len(plan), 1))
            emit_progress(on_progress, "transcribe", pct, f"正在识别 {i+1}/{len(plan)}…")

    # Drop overlap repetition lightly: if adjacent chunks share a long tail/head, keep later start unique
    raw_full = "\n".join(t.strip() for t in texts if t.strip())
    raw_full, collapsed = collapse_runaway_repeats(raw_full)
    if collapsed:
        log(f"REPEAT collapsed in joined raw runs={collapsed} chars={len(raw_full)}")
        texts = [collapse_runaway_repeats(t)[0] for t in texts]
    raw_out = raw_dir() / f"{stem}-{jid}.txt"
    raw_out.write_text(raw_full, encoding="utf-8")
    log(f"RAW saved {raw_out} chars={len(raw_full)}")

    # Retry subtitle discovery after ASR, preserving the existing proofreading fallback.
    if not track or not track.cues:
        try:
            track = subtitle_track or find_subtitles(src, job, extra_dirs=extras, prefer=lang)
        except Exception as exc:
            log(f"SUBTITLE retry failed, preserving ASR: {exc}")
            track = None
    proof_stats: dict = {}
    highlight_phrases: list[str] = []
    working = list(texts)
    if track and track.cues:
        emit_progress(on_progress, "proofread", 78, "正在用字幕校对…")
        triples = []
        for i, text in enumerate(texts):
            start, dur = plan[i] if i < len(plan) else (0.0, 0.0)
            triples.append((start, dur, text))
        results, proof_stats = proofread_chunks(triples, track, use_model=(polish != "none"))
        working = [r.text for r in results]
        highlight_phrases = list(dict.fromkeys(proof_stats.get("unresolved") or []))
        log(
            f"PROOFREAD source={track.source} cues={len(track.cues)} "
            f"corrections={proof_stats.get('corrections')} unresolved={len(highlight_phrases)}"
        )
    proof_note = format_proofread_note(proof_stats) if proof_stats else ""

    if lang == "auto":
        lang = infer_script("\n".join(working))
        log(f"LANG inferred {lang}")

    sections = []
    notes = []
    provider_used = "none"
    bilingual_pairs: list[dict] | None = None
    title_zh = ""
    if lang == "en":
        english_paras = [t.strip() for t in working if str(t).strip()]
        emit_progress(on_progress, "polish", 86, "正在翻译成中文…")
        if polish == "none":
            sections = [{"heading": "", "paragraphs": english_paras}]
            provider_used = "none"
            notes.append("未修饰，未翻译（可用设定或去掉 --no-polish 来翻译成中文）")
            log("ENGLISH --no-polish, skip translation")
        else:
            tr = translate_english(english_paras, provider=polish, title=stem)
            provider_used = tr.get("provider_used") or "none"
            if tr.get("note"):
                notes.append(tr["note"])
            bilingual_pairs = tr.get("pairs") or []
            title_zh = tr.get("title_zh") or ""
            zh_paras = [p.get("zh") or "" for p in bilingual_pairs if p.get("zh")]
            heading_zh = tr.get("heading_zh") or ""
            sections = [{"heading": heading_zh, "paragraphs": zh_paras or english_paras}]
            if not any(p.get("zh") for p in bilingual_pairs):
                bilingual_pairs = None
            if (cfg.get("english_output") or "bilingual") != "bilingual":
                bilingual_pairs = None
            log(f"TRANSLATE en->zh via {provider_used} pairs={len(tr.get('pairs') or [])}")
    else:
        emit_progress(on_progress, "polish", 86, "正在修饰文字…")
        for i, text in enumerate(working):
            if not text.strip():
                continue
            result = polish_text(text, provider=polish)
            provider_used = result["provider_used"]
            if result.get("note"):
                notes.append(result["note"])
            heading = result.get("heading") or ""
            if not bool(cfg.get("include_headings", False)):
                heading = ""
            sections.append({"heading": heading, "paragraphs": result["paragraphs"]})
            log(
                f"POLISH chunk {i+1}/{len(texts)} via {provider_used} "
                f"lead_chars={result.get('lead_chars') or 0}"
            )
            pct = 86 + int(8 * (i + 1) / max(len(working), 1))
            emit_progress(on_progress, "polish", min(pct, 94), f"正在修饰 {i+1}/{len(working)}…")

    summary = ""
    all_paras = [p for s in sections for p in s.get("paragraphs") or []]
    if all_paras and provider_used != "none" and bool(cfg.get("include_summary", False)):
        summary = make_summary(all_paras, provider_used)

    # If a disagreement is not visible in the body, append it so yellow highlights can show.
    body_text = "\n".join(p for s in sections for p in (s.get("paragraphs") or []))
    missing_marks = [ph for ph in highlight_phrases if ph and ph not in body_text]
    if missing_marks:
        sections.append({
            "heading": "未能确定的字幕对照",
            "paragraphs": [f"字幕与识别未能对齐：{ph}" for ph in missing_marks],
        })

    label_map = {"local": "本地千问", "claude": "Claude", "gpt": "GPT", "gemini": "Gemini", "none": "未修饰"}
    note = "；".join(dict.fromkeys(notes))
    include_raw = bool(cfg.get("include_raw_appendix", True))
    asr_label = asr_model_label(lang)
    emit_progress(on_progress, "write", 96, "正在生成 Word 文档…")
    docx_path = write_docx(
        out_dir / f"{stem}.docx",
        title=stem,
        source_name=source_name,
        sections=sections,
        summary=summary,
        note=note,
        lang="bo" if lang == "bo" else "zh",
        duration=duration,
        polish_label=label_map.get(provider_used, provider_used),
        asr_label=asr_label,
        raw_appendix=texts if include_raw else None,
        proofread_note=proof_note,
        highlight_phrases=highlight_phrases,
        title_zh=title_zh,
        bilingual_pairs=bilingual_pairs,
    )
    elapsed = time.perf_counter() - t0
    rtf_x = (duration / elapsed) if elapsed > 0 else 0.0
    log(f"DONE {src.name} -> {docx_path.name} audio={duration:.1f}s elapsed={elapsed:.1f}s ({rtf_x:.1f}x)")
    emit_progress(on_progress, "done", 100, "完成")
    if do_notify:
        notify("为我闻思", f"已完成「{docx_path.name}」，保存在下载文件夹")
    if open_docx:
        subprocess.run(["/usr/bin/open", str(docx_path)], check=False)
    return {
        "source": str(src),
        "docx": str(docx_path),
        "raw": str(raw_out),
        "text": raw_full,
        "polished_sections": sections,
        "summary": summary,
        "note": note,
        "duration": duration,
        "elapsed": elapsed,
        "rtf_x": rtf_x,
        "lang": lang,
        "provider_used": provider_used,
    }



def _safe_stem(name: str) -> str:
    name = (name or "transcript").strip()
    name = "".join(ch if ch.isalnum() or ch in "._-（）()《》【】 " else "_" for ch in name)
    name = name.strip(" ._") or "transcript"
    return name[:80]


def _transcribe_arg(
    value: str,
    lang: str,
    out_dir: Path,
    polish: Optional[str],
    do_notify: bool,
    open_docx: bool,
    on_progress=None,
) -> dict:
    if is_media_url(value):
        dest = work_dir() / "url-downloads" / hashlib.sha1(value.encode()).hexdigest()[:12]
        dest.mkdir(parents=True, exist_ok=True)
        log(f"DOWNLOAD {value}")
        emit_progress(on_progress, "download", 2, "正在获取视频和字幕…")
        info = download_url(value, dest)
        media: Path = info["media"]
        stem = _safe_stem(info.get("title") or media.stem)
        log(f"DOWNLOADED {media.name} subs={None if not info.get('track') else info['track'].source}")
        return transcribe_file(
            media,
            lang=lang,
            out_dir=out_dir,
            output_stem=stem,
            polish=polish,
            do_notify=do_notify,
            open_docx=open_docx,
            sidecar_dirs=[dest],
            subtitle_track=info.get("track"),
            on_progress=on_progress,
        )
    path = Path(value).expanduser()
    return transcribe_file(
        path,
        lang=lang,
        out_dir=out_dir,
        polish=polish,
        do_notify=do_notify,
        open_docx=open_docx,
        sidecar_dirs=[path.parent],
        on_progress=on_progress,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="transcribe", description="把音频转成 Word 文档，默认保存到下载文件夹。")
    parser.add_argument("files", nargs="*", help="音频/视频文件，或 YouTube 等网址（可多个）")
    parser.add_argument("--lang", default="auto", help="zh / en / bo / auto")
    parser.add_argument("--out", default=str(default_out_dir()), help="输出目录")
    parser.add_argument("--polish", default=None, help="local / claude / gpt / gemini / none（默认用设定）")
    parser.add_argument("--settings", action="store_true", help="打开设定")
    parser.add_argument("--open", dest="open_docx", action="store_true", help="完成后打开 Word 文档")
    parser.add_argument("--notify", action="store_true", help="开始和结束时发送系统通知")
    parser.add_argument("--gui", action="store_true", help="显示首次选择和设定对话框")
    parser.add_argument("--no-polish", action="store_true", help="只转写、不修饰")
    args = parser.parse_args(argv)

    if args.settings or (not args.files and args.gui):
        from gui import main as gui_main
        return gui_main([])

    if not args.files:
        from gui import main as gui_main
        return gui_main([])

    if args.no_polish:
        args.polish = "none"

    # CLI still uses saved settings; first launch defaults to local with no dialog.
    if args.polish is None:
        from settings import first_run_if_needed
        first_run_if_needed()

    rc = 0
    for f in args.files:
        try:
            result = _transcribe_arg(
                f,
                lang=args.lang,
                out_dir=Path(args.out),
                polish=args.polish,
                do_notify=args.notify,
                open_docx=args.open_docx,
            )
            print(result["docx"])
        except Exception as e:
            log(f"ERROR {f}: {e}")
            print(f"转写失败：{e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
