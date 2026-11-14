#!/usr/bin/env python3
"""Watch Desktop drop folders and transcribe new audio files via Finder."""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from paths import asr_home, logs_dir, work_dir
from transcribe import AUDIO_EXTS, transcribe_file

TZ = ZoneInfo("America/Los_Angeles")
DROP_ROOT_NAME = "语音转文字"
SUBFOLDERS = {"中文": "zh", "藏文": "bo"}
DONE_NAME = "完成"
IGNORE_SUFFIXES = {".download", ".crdownload", ".part", ".tmp", ".temp", ".icloud", ".srt", ".vtt", ".ass", ".ssa"}
POLL_SECONDS = 2.0
STABLE_SECONDS = 2.0
LIST_SCRIPT = asr_home() / "list_drops.applescript"


def log(msg: str) -> None:
    line = f"{datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')} {msg}"
    with (logs_dir() / "watch.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def osascript(script: str, timeout: int = 60) -> str:
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"osascript {proc.returncode}")
    return (proc.stdout or "").strip()


def ensure_drop_folders() -> None:
    script = f'''
tell application "Finder"
  if not (exists folder "{DROP_ROOT_NAME}" of desktop) then
    make new folder at desktop with properties {{name:"{DROP_ROOT_NAME}"}}
  end if
  set root to folder "{DROP_ROOT_NAME}" of desktop
  repeat with subName in {{"中文", "藏文", "{DONE_NAME}"}}
    if not (exists folder subName of root) then
      make new folder at root with properties {{name:(subName as string)}}
    end if
  end repeat
  return POSIX path of (root as alias)
end tell
'''
    try:
        log("Drop folder ready: " + osascript(script))
    except Exception as e:
        log(f"WARN could not ensure drop folders: {e}")


def list_drop_files() -> list[dict]:
    if LIST_SCRIPT.exists():
        proc = subprocess.run(["/usr/bin/osascript", str(LIST_SCRIPT)], capture_output=True, text=True, timeout=60)
        raw = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        if proc.returncode != 0:
            log(f"WARN list_drop_files: {(proc.stderr or '').strip()}")
    else:
        raw = ""
    items = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        folder, path, name, size_s = parts[0], parts[1], parts[2], parts[3]
        try:
            size = int(float(size_s))
        except ValueError:
            size = -1
        items.append({"folder": folder, "path": path, "name": name, "size": size})
    return items


def dest_has_file(dest_dir: Path, name: str) -> bool:
    path = dest_dir / name
    try:
        if path.exists():
            return True
        list(dest_dir.iterdir())
        return False
    except OSError:
        pass
    script = f'''
tell application "Finder"
  try
    set dest to POSIX file "{dest_dir}/" as alias
    return exists file "{name}" of folder dest
  on error
    return false
  end try
end tell
'''
    try:
        return osascript(script).lower() == "true"
    except Exception:
        return False


def finder_move(src: Path, dest_dir: Path, dest_name: str | None = None) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_name = dest_name or src.name
    if dest_has_file(dest_dir, dest_name):
        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        dest_path = Path(dest_name)
        dest_name = f"{dest_path.stem}-{stamp}{dest_path.suffix}"
    script = f'''
set src to POSIX file "{src}" as alias
set dest to POSIX file "{dest_dir}/" as alias
tell application "Finder"
  set moved to move src to dest
  set name of moved to "{dest_name}"
  return POSIX path of (moved as alias)
end tell
'''
    return Path(osascript(script))


def is_eligible(item: dict) -> bool:
    name = item["name"]
    if name.startswith("."):
        return False
    suffix = Path(item["path"]).suffix.lower()
    if suffix in IGNORE_SUFFIXES or any(name.lower().endswith(s) for s in IGNORE_SUFFIXES):
        return False
    return suffix in AUDIO_EXTS


def process_item(item: dict) -> None:
    src = Path(item["path"])
    original_name = item["name"]
    original_stem = Path(original_name).stem
    lang = SUBFOLDERS.get(item["folder"], "auto")
    log(f"Picked up {item['folder']}/{original_name} lang={lang}")
    work_dir().mkdir(parents=True, exist_ok=True)
    staged = finder_move(src, work_dir(), dest_name=original_name)
    try:
        result = transcribe_file(staged, lang=lang, output_stem=original_stem, do_notify=True, sidecar_dirs=[src.parent, staged.parent])
        log(f"Wrote {result['docx']}")
        done_dir = Path.home() / "Desktop" / DROP_ROOT_NAME / DONE_NAME
        final = finder_move(staged, done_dir, dest_name=original_name)
        log(f"Moved original to {final}")
    except Exception as e:
        log(f"ERROR processing {original_name}: {e}")
        try:
            err_path = staged.with_name(staged.stem + "-转写失败" + staged.suffix)
            if staged.exists():
                staged = staged.rename(err_path)
            finder_move(staged, Path.home() / "Desktop" / DROP_ROOT_NAME / DONE_NAME)
        except Exception as e2:
            log(f"WARN could not move failed original: {e2}")


def main() -> int:
    log("ASR watcher starting")
    ensure_drop_folders()
    seen: dict[str, tuple[int, float]] = {}
    in_flight: set[str] = set()
    while True:
        try:
            items = list_drop_files()
            now = time.monotonic()
            current = set()
            for item in items:
                if not is_eligible(item):
                    continue
                path = item["path"]
                current.add(path)
                if path in in_flight:
                    continue
                prev = seen.get(path)
                if prev is None or prev[0] != item["size"]:
                    seen[path] = (item["size"], now)
                    continue
                if now - prev[1] < STABLE_SECONDS:
                    continue
                in_flight.add(path)
                try:
                    process_item(item)
                finally:
                    in_flight.discard(path)
                    seen.pop(path, None)
            for stale in list(seen):
                if stale not in current:
                    seen.pop(stale, None)
        except Exception as e:
            log(f"WATCH LOOP ERROR: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("ASR watcher stopped")
        sys.exit(0)
