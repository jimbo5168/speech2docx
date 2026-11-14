#!/bin/bash
# Install Speech2Docx / 语音转文字 for the current user.
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
if [[ ! -f "$ASR_HOME/佛教词库.txt" ]]; then
  cp -f "$REPO_ROOT/佛教词库.txt" "$ASR_HOME/佛教词库.txt"
fi
if [[ ! -f "$ASR_HOME/config.json" ]]; then
  cp -f "$REPO_ROOT/config.example.json" "$ASR_HOME/config.json"
fi

# CLI wrapper
cat > "$ASR_HOME/transcribe" << WRAP
#!/bin/bash
export ASR_HOME="${ASR_HOME}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
exec "${ASR_HOME}/.venv/bin/python" "${ASR_HOME}/transcribe.py" "\$@"
WRAP
chmod +x "$ASR_HOME/transcribe" "$ASR_HOME/transcribe.py" "$ASR_HOME/watch.py"
ln -sfn "$ASR_HOME/transcribe" "$HOME/.local/bin/transcribe"

# Desktop drop folders via Finder (has Desktop TCC access)
osascript << 'APPLESCRIPT' >/dev/null || true
tell application "Finder"
  if not (exists folder "语音转文字" of desktop) then
    make new folder at desktop with properties {name:"语音转文字"}
  end if
  set root to folder "语音转文字" of desktop
  repeat with subName in {"中文", "藏文", "完成"}
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
  APP_PATH="/Applications/语音转文字.app"
else
  mkdir -p "$HOME/Applications"
  cp -R "$APP_BUILT" "$HOME/Applications/"
  APP_PATH="$HOME/Applications/语音转文字.app"
fi
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

# Dock
if [[ "$ADD_DOCK" == "1" ]]; then
  APP_URI="file://${APP_PATH}/"
  python3 - "$APP_URI" << 'PY' || true
import os, subprocess, sys
uri = sys.argv[1]
# Avoid adding a duplicate
existing = subprocess.check_output(["defaults", "read", "com.apple.dock", "persistent-apps"], text=True, stderr=subprocess.DEVNULL)
if "语音转文字.app" in existing or "org.speech2docx.app" in existing:
    print("Dock already has 语音转文字")
    sys.exit(0)
entry = f'''
<dict>
  <key>tile-data</key>
  <dict>
    <key>file-data</key>
    <dict>
      <key>_CFURLString</key>
      <string>{uri}</string>
      <key>_CFURLStringType</key>
      <integer>15</integer>
    </dict>
  </dict>
  <key>tile-type</key>
  <string>file-tile</string>
</dict>
'''
subprocess.run(["defaults", "write", "com.apple.dock", "persistent-apps", "-array-add", entry], check=True)
subprocess.run(["killall", "Dock"], check=False)
print("Added to Dock")
PY
fi

# PATH hint
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) log "Add this to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

log "Install complete."
log "Usage: drop audio on the 语音转文字 app or Desktop folder, or run: transcribe FILE.mp3"
