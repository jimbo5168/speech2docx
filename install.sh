#!/bin/bash
# Install 为我闻思 (Wei Wo Wen Si) / speech2docx for the current user.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
ASR_HOME="${ASR_HOME:-$HOME/ASR}"
PYTHON_BIN="${PYTHON_BIN:-}"
ADD_DOCK="${ADD_DOCK:-1}"

log() { printf '%s\n' "$*"; }

if [[ -z "$PYTHON_BIN" ]]; then
  for c in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if [[ -x "$c" ]]; then PYTHON_BIN="$c"; break; fi
  done
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Need Python 3.10+." >&2
  exit 1
fi

log "Installing into $ASR_HOME"
mkdir -p "$ASR_HOME"/{logs,work,models} "$HOME/.local/bin" "$HOME/Library/LaunchAgents"

# venv
if [[ ! -x "$ASR_HOME/.venv/bin/python" ]]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "$ASR_HOME/.venv" --python "$PYTHON_BIN"
  else
    "$PYTHON_BIN" -m venv "$ASR_HOME/.venv"
  fi
fi
VPY="$ASR_HOME/.venv/bin/python"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VPY" -r "$REPO_ROOT/requirements.txt"
else
  "$VPY" -m pip install -U pip
  "$VPY" -m pip install -r "$REPO_ROOT/requirements.txt"
fi

# Copy runtime sources
cp -f "$REPO_ROOT"/src/*.py "$ASR_HOME/"
cp -f "$REPO_ROOT/scripts/list_drops.applescript" "$ASR_HOME/list_drops.applescript"
rm -rf "$ASR_HOME/ui"
mkdir -p "$ASR_HOME/ui"
cp -f "$REPO_ROOT"/ui/* "$ASR_HOME/ui/"
if [[ ! -f "$ASR_HOME/佛教词库.txt" ]]; then
  cp -f "$REPO_ROOT/佛教词库.txt" "$ASR_HOME/佛教词库.txt"
fi
if [[ ! -f "$ASR_HOME/config.json" ]]; then
  cp -f "$REPO_ROOT/config.example.json" "$ASR_HOME/config.json"
fi

# Bundled Word template (layout + fonts only)
mkdir -p "$ASR_HOME/templates"
if [[ -f "$REPO_ROOT/templates/default.docx" ]]; then
  cp -f "$REPO_ROOT/templates/default.docx" "$ASR_HOME/templates/default.docx"
fi


# CLI wrapper
cat > "$ASR_HOME/transcribe" << WRAP
#!/bin/bash
export ASR_HOME="${ASR_HOME}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
exec "${ASR_HOME}/.venv/bin/python" "${ASR_HOME}/transcribe.py" "\$@"
WRAP
# gui-launch is rewritten after the .app is installed so it can use 为我闻思.
cat > "$ASR_HOME/gui-launch" << WRAP
#!/bin/bash
export ASR_HOME="${ASR_HOME}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
exec "${ASR_HOME}/.venv/bin/python" "${ASR_HOME}/gui.py" "\$@"
WRAP
chmod +x "$ASR_HOME/transcribe" "$ASR_HOME/transcribe.py" "$ASR_HOME/watch.py" "$ASR_HOME/gui.py" "$ASR_HOME/gui-launch"
ln -sfn "$ASR_HOME/transcribe" "$HOME/.local/bin/transcribe"

# Desktop drop folders via Finder (has Desktop TCC access)
osascript << 'APPLESCRIPT' >/dev/null || true
tell application "Finder"
  if not (exists folder "语音转文字" of desktop) then
    make new folder at desktop with properties {name:"语音转文字"}
  end if
  set root to folder "语音转文字" of desktop
  repeat with subName in {"中文", "英文", "藏文", "完成"}
    if not (exists folder subName of root) then
      make new folder at root with properties {name:(subName as string)}
    end if
  end repeat
end tell
APPLESCRIPT

# Models
if command -v hf >/dev/null 2>&1; then
  HF=hf
elif [[ -x "$HOME/.local/bin/hf" ]]; then
  HF="$HOME/.local/bin/hf"
else
  HF="$VPY -m huggingface_hub.cli.hf"
fi
log "Downloading models (skipped if already cached)…"
# shellcheck disable=SC2086
$HF download mlx-community/Qwen3-ASR-1.7B-8bit
$HF download tenzin/whisper-small-tibetan-ct2

# App
log "Building app…"
APP_BUILT="$("$REPO_ROOT/scripts/build_app.sh" "$ASR_HOME/dist")"
if mkdir -p /Applications && cp -R "$APP_BUILT" "/Applications/" 2>/dev/null; then
  APP_PATH="/Applications/为我闻思.app"
else
  mkdir -p "$HOME/Applications"
  cp -R "$APP_BUILT" "$HOME/Applications/"
  APP_PATH="$HOME/Applications/为我闻思.app"
fi
# Replace the previous app name if it is still installed.
rm -rf "/Applications/语音转文字.app" "$HOME/Applications/语音转文字.app"
# Refresh Launch Services so Finder/Dock pick up the new name and icon.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_PATH" >/dev/null 2>&1 || true
# Point the GUI launcher at the native engine inside the .app (not Python.app).
"$VPY" - "$ASR_HOME" "$APP_PATH" "$VPY" << 'PY'
from pathlib import Path
import sys
asr_home, app_path, vpy = sys.argv[1], sys.argv[2], sys.argv[3]
gui_py = str(Path(asr_home) / "gui.py")
engine = str(Path(app_path) / "Contents/MacOS/为我闻思")
script = Path(asr_home) / "gui-launch"
lines = [
    "#!/bin/bash",
    f"export ASR_HOME={asr_home!r}",
    'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"',
    f"ENGINE={engine!r}",
    'if [[ -x "$ENGINE" ]]; then',
    f'  exec "$ENGINE" {gui_py!r} "$@"',
    "fi",
    f"exec {vpy!r} {gui_py!r} \"$@\"",
    "",
]
script.write_text("\n".join(lines))
script.chmod(0o755)
PY
log "App installed at $APP_PATH"

# launchd
PLIST="$HOME/Library/LaunchAgents/com.speech2docx.watch.plist"
sed -e "s#__ASR_HOME__#${ASR_HOME}#g" -e "s#__HOME__#${HOME}#g" \
  "$REPO_ROOT/launchd/com.speech2docx.watch.plist.template" > "$PLIST"
uid="$(id -u)"
launchctl bootout "gui/$uid/com.speech2docx.watch" 2>/dev/null || true
# Stop a previous local agent if the user is upgrading from an earlier install
launchctl bootout "gui/$uid/com.speech2docx.asr-watch" 2>/dev/null || true
launchctl bootstrap "gui/$uid" "$PLIST"
launchctl enable "gui/$uid/com.speech2docx.watch" 2>/dev/null || true

# Dock: drop the old 语音转文字 tile and pin 为我闻思.
if [[ "$ADD_DOCK" == "1" ]]; then
  python3 - "$APP_PATH" << 'PY' || true
import subprocess, sys, tempfile, plistlib
from pathlib import Path
from urllib.parse import unquote, urlparse

app_path = str(Path(sys.argv[1]).resolve())
app_uri = Path(app_path).as_uri() + "/"
legacy_names = ("语音转文字.app", "为我闻思.app")
bundle_id = "org.speech2docx.app"

def tile_ref(tile):
    data = (tile or {}).get("tile-data") or {}
    file_data = data.get("file-data") or {}
    url = file_data.get("_CFURLString") or data.get("file-label") or ""
    ident = data.get("bundle-identifier") or ""
    try:
        path = unquote(urlparse(url).path) if "://" in str(url) else str(url)
    except Exception:
        path = str(url)
    return path, ident, str(url)

exported = Path(tempfile.mkstemp(suffix=".plist")[1])
try:
    subprocess.run(["defaults", "export", "com.apple.dock", str(exported)], check=True)
    with exported.open("rb") as fh:
        dock = plistlib.load(fh)
    apps = list(dock.get("persistent-apps") or [])
    kept = []
    changed = False
    already = False
    for tile in apps:
        path, ident, url = tile_ref(tile)
        is_ours = ident == bundle_id or any(name in path or name in unquote(url) for name in legacy_names)
        if is_ours:
            if path.rstrip("/") == app_path:
                already = True
                kept.append(tile)
            else:
                changed = True
            continue
        kept.append(tile)
    if not already:
        kept.append({
            "tile-type": "file-tile",
            "tile-data": {
                "file-data": {
                    "_CFURLString": app_uri,
                    "_CFURLStringType": 15,
                }
            },
        })
        changed = True
    if changed:
        dock["persistent-apps"] = kept
        with exported.open("wb") as fh:
            plistlib.dump(dock, fh)
        subprocess.run(["defaults", "import", "com.apple.dock", str(exported)], check=True)
        subprocess.run(["killall", "Dock"], check=False)
        print("Updated Dock for 为我闻思")
    else:
        print("Dock already has 为我闻思")
finally:
    try:
        exported.unlink()
    except OSError:
        pass
PY
fi

# PATH hint
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) log "Add this to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

log "Install complete."
log "Usage: open 为我闻思, or drop audio on the app / Desktop/语音转文字 folder, or run: transcribe FILE.mp3"
