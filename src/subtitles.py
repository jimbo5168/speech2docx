"""Find, parse, and align subtitles with ASR text for proofreading."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from glossary import load_terms
from paths import FFMPEG, FFPROBE, work_dir

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be", "music.youtube.com"}


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class SubtitleTrack:
    cues: list[Cue]
    source: str  # human-readable
    trust: str  # official | sidecar | embedded | auto
    path: Optional[Path] = None
    language: str = ""


def is_media_url(value: str) -> bool:
    text = (value or "").strip()
    if not re.match(r"^https?://", text, re.I):
        return False
    host = (urlparse(text).hostname or "").lower()
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return True
    # other http(s) media yt-dlp can handle
    return True


def is_youtube_url(value: str) -> bool:
    host = (urlparse((value or "").strip()).hostname or "").lower()
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _ts_to_seconds(stamp: str) -> float:
    stamp = stamp.strip().replace(",", ".")
    parts = stamp.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = "0"
        m, s = parts
    else:
        return 0.0
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # optional index line
        if re.match(r"^\d+$", lines[0].strip()) and len(lines) >= 2:
            lines = lines[1:]
        if not lines:
            continue
        m = re.search(
            r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
            r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})",
            lines[0],
        )
        if not m:
            continue
        body = _clean_sub_text("\n".join(lines[1:]))
        if body:
            cues.append(Cue(_ts_to_seconds(m.group(1)), _ts_to_seconds(m.group(2)), body))
    return cues


def parse_vtt(text: str) -> list[Cue]:
    # Drop header and NOTE/STYLE blocks loosely, then reuse SRT-like parsing
    cleaned = re.sub(r"^(WEBVTT[^\n]*\n)+", "", text.replace("\r\n", "\n"), flags=re.I)
    cleaned = re.sub(r"\n(NOTE|STYLE|REGION)[^\n]*(\n[^\n]*)*\n", "\n\n", cleaned)
    return parse_srt(cleaned)


def parse_ass(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.startswith("Dialogue:"):
            continue
        # Dialogue: marked, start, end, style, name, marginL, marginR, marginV, effect, text
        try:
            payload = line.split(":", 1)[1]
            parts = payload.split(",", 9)
            if len(parts) < 10:
                continue
            start = _ts_to_seconds(parts[1].strip())
            end = _ts_to_seconds(parts[2].strip())
            body = _clean_sub_text(re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", "\n").replace("\\n", "\n"))
            if body:
                cues.append(Cue(start, end, body))
        except Exception:
            continue
    return cues


def parse_subtitles(path: Path) -> list[Cue]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".vtt":
        return parse_vtt(raw)
    if suffix in {".ass", ".ssa"}:
        return parse_ass(raw)
    return parse_srt(raw)


def _clean_sub_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sidecar_candidates(src: Path, extra_dirs: list[Path] | None = None) -> list[Path]:
    dirs = [src.parent]
    for d in extra_dirs or []:
        if d and d not in dirs:
            dirs.append(d)
    stems = [src.stem]
    # "name.zh" / "name.en" already in stem when suffix is .srt; also try base before last dot
    if "." in src.stem:
        stems.append(src.stem.split(".")[0])
    langs = ["", ".zh", ".zh-Hans", ".zh-CN", ".zh-Hant", ".chi", ".en", ".eng"]
    exts = [".srt", ".vtt", ".ass", ".ssa"]
    out: list[Path] = []
    seen: set[Path] = set()
    for folder in dirs:
        for stem in stems:
            for lang in langs:
                for ext in exts:
                    cand = folder / f"{stem}{lang}{ext}"
                    if cand.exists() and cand not in seen:
                        seen.add(cand)
                        out.append(cand)
    return out


def load_sidecar(src: Path, extra_dirs: list[Path] | None = None) -> Optional[SubtitleTrack]:
    for path in sidecar_candidates(src, extra_dirs):
        try:
            cues = parse_subtitles(path)
        except (OSError, ValueError):
            continue
        if cues:
            lang = ""
            low = path.name.lower()
            for token, code in ((".zh", "zh"), (".chi", "zh"), (".en", "en"), (".eng", "en")):
                if token in low:
                    lang = code
                    break
            return SubtitleTrack(cues=cues, source=f"同名字幕文件（{path.name}）", trust="sidecar", path=path, language=lang)
    return None


def list_embedded_subtitle_streams(src: Path) -> list[dict[str, Any]]:
    try:
        out = subprocess.check_output(
            [FFPROBE, "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=index,codec_name:stream_tags=language,title,handler_name",
             "-of", "json", str(src)],
            stderr=subprocess.STDOUT, text=True, timeout=5,
        )
        data = json.loads(out or "{}")
        return list(data.get("streams") or [])
    except Exception:
        return []


def _lang_of_stream(stream: dict) -> str:
    tags = stream.get("tags") or {}
    return str(tags.get("language") or tags.get("title") or "").lower()


def extract_embedded_subtitles(src: Path, dest: Path, prefer: str = "zh") -> Optional[SubtitleTrack]:
    streams = list_embedded_subtitle_streams(src)
    if not streams:
        return None
    prefer = (prefer or "zh").lower()
    def score(s: dict) -> tuple:
        lang = _lang_of_stream(s)
        zh = 0 if lang.startswith("zh") or lang in {"chi", "zho", "cn"} else 1
        en = 0 if lang.startswith("en") else 1
        if prefer.startswith("en"):
            return (en, zh, s.get("index", 99))
        return (zh, en, s.get("index", 99))
    streams = sorted(streams, key=score)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stream in streams:
        if not is_text_subtitle_codec(stream.get("codec_name", "")):
            continue
        idx = stream.get("index")
        out = dest.with_suffix(".srt")
        try:
            proc = subprocess.run(
                [FFMPEG, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
                 "-map", f"0:{idx}", "-c:s", "srt", str(out)],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0 and out.exists():
                cues = parse_subtitles(out)
                if cues:
                    lang = _lang_of_stream(stream) or prefer
                    return SubtitleTrack(cues, f"视频内嵌字幕（{lang}）", "embedded", out, lang)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None


def is_text_subtitle_codec(codec: str) -> bool:
    # FFmpeg text decoders can export SRT. Reject bitmap and unknown tracks.
    return codec.lower() in {
        "mov_text", "subrip", "srt", "ass", "ssa", "webvtt", "text",
        "microdvd", "mpl2", "jacosub", "pjs", "realtext", "sami", "stl",
        "subviewer", "subviewer1", "vplayer", "scc", "eia_608", "ttml",
        "hdmv_text_subtitle", "arib_caption", "libaribb24", "libaribcaption",
    }


def export_cues_to_srt(cues: list[Cue], dest: Path) -> Path:
    def stamp(seconds: float) -> str:
        millis = max(0, round(seconds * 1000))
        seconds, ms = divmod(millis, 1000)
        minutes, sec = divmod(seconds, 60)
        hours, minute = divmod(minutes, 60)
        return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"

    blocks = [f"{i}\n{stamp(c.start)} --> {stamp(c.end)}\n{c.text.strip()}\n"
              for i, c in enumerate(cues, 1)]
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(blocks), encoding="utf-8")
    return dest


def find_yt_dlp() -> str:
    from shutil import which
    from paths import asr_home
    for c in (which("yt-dlp"), str(asr_home() / ".venv" / "bin" / "yt-dlp"), "/opt/homebrew/bin/yt-dlp"):
        if c and Path(c).exists():
            return c
    return "yt-dlp"


def download_url(url: str, dest_dir: Path) -> dict[str, Any]:
    """Download audio (and video if needed) plus subtitles. Official subs preferred."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = find_yt_dlp()
    outtmpl = str(dest_dir / "%(id)s.%(ext)s")
    # Inspect metadata first. Subtitle filenames do not encode provenance.
    metadata_cmd = [ytdlp, "--skip-download", "--write-info-json", "--no-playlist",
                    "--js-runtimes", "node", "-o", outtmpl, url]
    proc = subprocess.run(metadata_cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("读取视频信息失败：" + (proc.stderr or proc.stdout or "")[-800:])
    infos = sorted(dest_dir.glob("*.info.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not infos:
        raise RuntimeError("下载器没有生成视频信息。")
    info = json.loads(infos[0].read_text(encoding="utf-8"))
    def preferred(tracks):
        languages = [lang for lang in tracks if lang.startswith("zh") or lang == "en"]
        return sorted(languages, key=lambda lang: (lang != "zh", not lang.startswith("zh"), lang))
    human = preferred(info.get("subtitles") or {})
    automatic = preferred(info.get("automatic_captions") or {})
    trust = "official" if human else "auto"
    languages = human or automatic
    # Separate folders prevent stale auto files from becoming trusted sidecars.
    sub_dir = dest_dir / trust
    sub_dir.mkdir(parents=True, exist_ok=True)
    cmd = [ytdlp, "--no-progress", "--newline", "-f", "bv*+ba/b/ba",
           "--merge-output-format", "mp4", "-o", outtmpl, "--no-playlist",
           "--js-runtimes", "node", url]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("下载失败：" + (proc.stderr or proc.stdout or "")[-800:])
    if languages:
        cmd = [ytdlp, "--skip-download", "--no-playlist", "--js-runtimes", "node",
               "--write-subs" if human else "--write-auto-subs",
               "--sub-langs", languages[0], "--convert-subs", "srt",
               "-o", str(sub_dir / "%(id)s.%(ext)s"), url]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError("下载字幕失败：" + (proc.stderr or proc.stdout or "")[-800:])
    media = None
    for ext in (".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".opus"):
        cands = sorted(dest_dir.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            media = cands[0]
            break
    if media is None:
        raise RuntimeError("下载完成但没有找到音视频文件。")
    track = None
    candidates = sorted(sub_dir.glob("*.srt"))
    if candidates:
        chosen = candidates[0]
        cues = parse_subtitles(chosen)
        if cues:
            label = "YouTube 官方字幕" if trust == "official" else "YouTube 自动字幕（可信度较低）"
            track = SubtitleTrack(cues, label, trust, chosen, languages[0])
    return {"media": media, "track": track, "title": info.get("title") or media.stem, "dir": dest_dir}


def find_subtitles(
    src: Path,
    job_dir: Path,
    extra_dirs: list[Path] | None = None,
    prefer: str = "zh",
) -> Optional[SubtitleTrack]:
    sidecar = load_sidecar(src, extra_dirs)
    if sidecar:
        return sidecar
    embedded = extract_embedded_subtitles(src, job_dir / "embedded-subs", prefer=prefer)
    if embedded:
        return embedded
    return None


def cues_in_window(cues: list[Cue], start: float, end: float, slack: float = 0.35) -> list[Cue]:
    out = []
    for c in cues:
        if c.end > start - slack and c.start < end + slack:
            out.append(c)
    return out


def join_cues(cues: list[Cue]) -> str:
    parts = [c.text.strip() for c in cues if c.text.strip()]
    return "".join(parts) if all(_mostly_cjk(p) for p in parts) else " ".join(parts)


def _mostly_cjk(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha() or "\u4e00" <= ch <= "\u9fff"]
    if not letters:
        return True
    cjk = sum(1 for ch in letters if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(letters) >= 0.5


def _norm(text: str) -> str:
    text = re.sub(r"[\s，。、“”‘’！？；：、,.!?;:\"']+", "", text)
    return text.lower()


def _glossary_hits(text: str) -> set[str]:
    terms = load_terms(400)
    return {t for t in terms if t and t in text}


@dataclass
class ProofreadResult:
    text: str
    corrections: int = 0
    model_judged: bool = False
    unresolved: list[str] = field(default_factory=list)
    note: str = ""


def reconcile_chunk(
    asr_text: str,
    cues: list[Cue],
    prefer_subs: bool = True,
    use_model: bool = True,
    trust: str = "official",
) -> ProofreadResult:
    asr_text = (asr_text or "").strip()
    sub_text = join_cues(cues).strip()
    if not sub_text:
        return ProofreadResult(text=asr_text)
    if not asr_text:
        return ProofreadResult(text=sub_text, corrections=1, note="此段仅有字幕、识别为空，已采用字幕。")

    if _norm(asr_text) == _norm(sub_text):
        return ProofreadResult(text=sub_text if prefer_subs else asr_text)

    # Auto captions are lower trust: only take glossary fixes and obvious short repairs
    low_trust = trust == "auto"

    # Glossary: if subtitle contains a term that ASR missed / distorted, lean subtitle
    sub_terms = _glossary_hits(sub_text)
    asr_terms = _glossary_hits(asr_text)

    if not low_trust and prefer_subs:
        # If texts are close (high char overlap), prefer subtitle wording
        if _similar(asr_text, sub_text) and len(sub_text) >= max(8, int(len(asr_text) * 0.5)):
            return ProofreadResult(text=sub_text, corrections=1)

    if use_model:
        judged = _model_judge(asr_text, sub_text, trust)
        if judged is not None:
            return judged

    if not low_trust and prefer_subs:
        return ProofreadResult(text=sub_text, corrections=1, note="已优先采用字幕措辞。")
    # Keep ASR, mark disagreement
    snippet = sub_text[:40] + ("…" if len(sub_text) > 40 else "")
    marked = asr_text
    return ProofreadResult(
        text=marked,
        unresolved=[sub_text] if sub_text else [],
        note=f"字幕与识别不一致，已保留识别并黄标对照：{snippet}",
    )


def _similar(a: str, b: str, threshold: float = 0.55) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    # char-level Jaccard on bigrams
    def grams(s: str) -> set[str]:
        if len(s) < 2:
            return {s}
        return {s[i : i + 2] for i in range(len(s) - 1)}
    ga, gb = grams(na), grams(nb)
    inter = len(ga & gb)
    union = len(ga | gb) or 1
    return inter / union >= threshold


def _model_judge(asr_text: str, sub_text: str, trust: str) -> Optional[ProofreadResult]:
    try:
        from polish import _call_backend, load_config, SYSTEM_PROMPT  # type: ignore
    except Exception:
        try:
            from polish import polish_local, load_config
        except Exception:
            return None
    try:
        from config import load_config
        from polish import _call_backend
    except Exception:
        return None

    cfg = load_config()
    system = (
        "你是佛教讲记的校对编辑。下面有同一时段的「语音识别初稿」和「字幕」。"
        "请输出 JSON，字段为 text（校对后的正文）、corrections（整数，你改了几处）、"
        "unresolved（字符串数组：无法判定的分歧短语）。"
        "规则：字幕可信时优先采用字幕措辞；自动字幕可信度较低，只在明显听写错误时才改识别稿。"
        "用佛教词库里的正确用词（如违缘、出离心、菩提心）。"
        "不要摘要，不要添加原文没有的意思。无法判定的短语原样放进 unresolved。"
        "只输出 JSON。"
    )
    if trust == "auto":
        system += "本次字幕是自动生成的，请更谨慎，不要全盘照搬字幕。"
    prompt = f"语音识别初稿：\n{asr_text}\n\n字幕：\n{sub_text}\n"
    try:
        raw = _call_backend("local", "login", prompt, system, cfg)
    except Exception:
        return None
    from polish import _parse_json_obj
    obj = _parse_json_obj(raw) or {}
    # _parse_json_obj requires paragraphs; this JSON uses text. Parse manually.
    if not obj or "text" not in obj:
        try:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                obj = json.loads(raw[start : end + 1])
        except Exception:
            return None
    text = str(obj.get("text") or "").strip()
    if not text:
        return None
    unresolved = [str(x).strip() for x in (obj.get("unresolved") or []) if str(x).strip()]
    try:
        corrections = int(obj.get("corrections") or 0)
    except Exception:
        corrections = 1
    return ProofreadResult(text=text, corrections=max(corrections, 1), model_judged=True, unresolved=unresolved)


def proofread_chunks(
    chunks: list[tuple[float, float, str]],
    track: SubtitleTrack,
    use_model: bool = True,
) -> tuple[list[ProofreadResult], dict]:
    """chunks: list of (start, duration, asr_text)."""
    results: list[ProofreadResult] = []
    total_corr = 0
    model_n = 0
    unresolved: list[str] = []
    compared = 0
    for start, dur, asr in chunks:
        end = start + dur
        window = cues_in_window(track.cues, start, end)
        if window:
            compared += 1
        r = reconcile_chunk(asr, window, trust=track.trust, use_model=use_model)
        results.append(r)
        total_corr += r.corrections
        if r.model_judged:
            model_n += 1
        unresolved.extend(r.unresolved)
    stats = {
        "source": track.source,
        "trust": track.trust,
        "cues": len(track.cues),
        "chunks_compared": compared,
        "corrections": total_corr,
        "model_judged": model_n,
        "unresolved": unresolved,
    }
    return results, stats


def format_proofread_note(stats: dict) -> str:
    if not stats:
        return ""
    lines = [
        f"字幕来源：{stats.get('source') or '无'}",
        f"字幕条数：{stats.get('cues', 0)}，对照片段：{stats.get('chunks_compared', 0)}",
        f"采纳或修正：{stats.get('corrections', 0)} 处；模型裁定：{stats.get('model_judged', 0)} 段",
    ]
    un = stats.get("unresolved") or []
    if un:
        lines.append(f"未能确定（已在正文用黄色标出）：{len(un)} 处")
    if stats.get("trust") == "auto":
        lines.append("本次使用自动字幕，可信度较低，请以正文黄标和附录原文复核。")
    return "\n".join(lines)
