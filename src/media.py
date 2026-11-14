"""Audio/video helpers: recognize media types and extract a 16 kHz mono WAV."""

from __future__ import annotations

import subprocess
from pathlib import Path

from paths import FFMPEG, FFPROBE

AUDIO_EXTS = {
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".caf", ".aiff", ".aif", ".m4b", ".amr",
}
VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi",
    ".mpeg", ".mpg", ".3gp", ".wmv", ".flv", ".ts", ".mts",
}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS


def media_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            stderr=subprocess.STDOUT, text=True,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def extract_audio(src: Path, dst: Path) -> Path:
    """Extract or convert the soundtrack to 16 kHz mono PCM WAV.

    Works for both audio and video inputs. Video picture streams are dropped.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-sample_fmt", "s16",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = err[-8:] if err else ["unknown ffmpeg error"]
        raise RuntimeError(f"无法从「{src.name}」抽出音频：\n" + "\n".join(tail))
    return dst
