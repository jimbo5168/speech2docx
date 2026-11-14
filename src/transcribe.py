#!/usr/bin/env python3
"""Transcribe audio to a polished Word document in ~/Downloads."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
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
from glossary import as_hotwords, as_prompt_block, ensure_glossary
from notify import notify
from media import MEDIA_EXTS, extract_audio, is_video, media_duration
from subtitles import (
    download_url,
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
from polish import make_summary, polish_text, translate_english
from template import write_from_template

TZ = ZoneInfo("America/Los_Angeles")
LOCK_PATH = logs_dir() / "transcribe.lock"
QWEN_MODEL_ID = "mlx-community/Qwen3-ASR-1.7B-8bit"
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
        return _opencc.convert(text)
    except Exception:
        return text


def load_qwen():
    global _qwen_model
    if _qwen_model is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from mlx_audio.stt import load
        path = resolve_hub_snapshot(QWEN_MODEL_ID)
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


def transcribe_qwen(wav: Path, lang: Optional[str]) -> str:
    model = load_qwen()
    kwargs = {"verbose": False, "hotwords": as_hotwords(250)}
    block = as_prompt_block(2000)
    if block:
        kwargs["system_prompt"] = "请准确识别佛教讲课中的专有名词，例如：" + block
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
    r = meta.add_run(f"来源：{source_name}    时长：{format_duration(duration)}    转写时间：{when}    修饰：{polish_label}")
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
    raw_chunks = job / "raw"
    wav_dir = job / "wav"
    raw_chunks.mkdir(parents=True, exist_ok=True)
    wav_dir.mkdir(parents=True, exist_ok=True)

    media_src = src
    if is_video(src):
        extracted = job / "source.wav"
        if not extracted.exists() or extracted.stat().st_size == 0:
            log(f"EXTRACT audio from video {src.name}")
            extract_audio(src, extracted)
        media_src = extracted
        log(f"VIDEO soundtrack ready: {extracted.name}")

    duration = audio_duration(media_src) or media_duration(src)
    if do_notify:
        notify("语音转文字", f"开始转写「{source_name}」（约 {int(duration//60)} 分钟）")

    log(f"START {src.name} lang={lang} duration={duration:.1f}s")
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
            if raw_path.exists() and raw_path.stat().st_size > 0:
                texts.append(raw_path.read_text(encoding="utf-8"))
                log(f"RESUME chunk {i+1}/{len(plan)}")
                continue
            wav = wav_dir / f"{i:03d}.wav"
            to_wav16k(media_src, wav, start=start, duration=dur)
            if lang == "bo":
                text = transcribe_tibetan(wav)
            else:
                text = transcribe_qwen(wav, lang)
            raw_path.write_text(text, encoding="utf-8")
            texts.append(text)
            try:
                wav.unlink()
            except OSError:
                pass
            log(f"ASR chunk {i+1}/{len(plan)} start={start:.0f}s chars={len(text)}")

    # Drop overlap repetition lightly: if adjacent chunks share a long tail/head, keep later start unique
    raw_full = "\n".join(t.strip() for t in texts if t.strip())
    raw_out = raw_dir() / f"{stem}-{jid}.txt"
    raw_out.write_text(raw_full, encoding="utf-8")
    log(f"RAW saved {raw_out} chars={len(raw_full)}")

    extras = list(sidecar_dirs or [])
    extras.append(src.parent)
    track = subtitle_track or find_subtitles(src, job, extra_dirs=extras, prefer=lang if lang in {"zh", "en", "bo"} else "zh")
    proof_stats: dict = {}
    highlight_phrases: list[str] = []
    working = list(texts)
    if track and track.cues:
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
        for i, text in enumerate(working):
            if not text.strip():
                continue
            result = polish_text(text, provider=polish)
            provider_used = result["provider_used"]
            if result.get("note"):
                notes.append(result["note"])
            sections.append({"heading": result.get("heading") or "", "paragraphs": result["paragraphs"]})
            log(f"POLISH chunk {i+1}/{len(texts)} via {provider_used}")

    summary = ""
    all_paras = [p for s in sections for p in s.get("paragraphs") or []]
    if all_paras and provider_used != "none":
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
        raw_appendix=texts if include_raw else None,
        proofread_note=proof_note,
        highlight_phrases=highlight_phrases,
        title_zh=title_zh,
        bilingual_pairs=bilingual_pairs,
    )
    elapsed = time.perf_counter() - t0
    rtf_x = (duration / elapsed) if elapsed > 0 else 0.0
    log(f"DONE {src.name} -> {docx_path.name} audio={duration:.1f}s elapsed={elapsed:.1f}s ({rtf_x:.1f}x)")
    if do_notify:
        notify("语音转文字", f"已完成「{docx_path.name}」，保存在下载文件夹")
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
) -> dict:
    if is_media_url(value):
        dest = work_dir() / "url-downloads" / hashlib.sha1(value.encode()).hexdigest()[:12]
        dest.mkdir(parents=True, exist_ok=True)
        log(f"DOWNLOAD {value}")
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

    if args.settings or not args.files:
        from settings import run_settings_menu
        run_settings_menu()
        if not args.files:
            return 0

    if args.no_polish:
        args.polish = "none"

    # First-run chooser only when the user is in an interactive app/CLI without --polish
    if args.polish is None and (args.gui or sys.stdin.isatty()):
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
