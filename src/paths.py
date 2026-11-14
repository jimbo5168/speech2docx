"""Runtime directories. No machine-specific usernames are hardcoded."""

from __future__ import annotations

import os
from pathlib import Path


def asr_home() -> Path:
    return Path(os.environ.get("ASR_HOME", Path.home() / "ASR")).expanduser()


def logs_dir() -> Path:
    p = asr_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def raw_dir() -> Path:
    p = logs_dir() / "raw"
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir() -> Path:
    p = asr_home() / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def jobs_dir() -> Path:
    p = work_dir() / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return asr_home() / "config.json"


def glossary_path() -> Path:
    return asr_home() / "佛教词库.txt"


def default_out_dir() -> Path:
    return Path.home() / "Downloads"


FFMPEG = os.environ.get("FFMPEG", "/opt/homebrew/bin/ffmpeg")
if not Path(FFMPEG).exists():
    import shutil

    FFMPEG = shutil.which("ffmpeg") or "ffmpeg"

FFPROBE = os.environ.get("FFPROBE", "/opt/homebrew/bin/ffprobe")
if not Path(FFPROBE).exists():
    import shutil

    FFPROBE = shutil.which("ffprobe") or "ffprobe"
