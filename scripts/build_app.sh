#!/bin/bash
# Build 为我闻思.app into a destination directory.
set -euo pipefail
ASR_HOME="${ASR_HOME:-$HOME/ASR}"
DEST_DIR="${1:-$ASR_HOME/dist}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="为我闻思.app"
WORKDIR="$(mktemp -d)"
SCRIPT="$WORKDIR/droplet.applescript"
sed "s#__ASR_HOME__#${ASR_HOME}#g" "$REPO_ROOT/scripts/droplet.applescript.template" > "$SCRIPT"
mkdir -p "$DEST_DIR"
APP="$DEST_DIR/$APP_NAME"
rm -rf "$APP"
/usr/bin/osacompile -o "$APP" "$SCRIPT"
# Icon
ICON="$WORKDIR/AppIcon.icns"
if /usr/bin/python3 "$REPO_ROOT/scripts/make_icon.py" "$ICON" 2>/dev/null; then
  mkdir -p "$APP/Contents/Resources"
  cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$APP/Contents/Info.plist"
fi
# Identifier + document types so it is a droplet
INFO="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.speech2docx.app" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string org.speech2docx.app" "$INFO"
/usr/libexec/PlistBuddy -c "Set :CFBundleName 为我闻思" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string 为我闻思" "$INFO"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 为我闻思" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 为我闻思" "$INFO"

# Accept audio drops on the Dock icon
/usr/libexec/PlistBuddy -c "Delete :CFBundleDocumentTypes" "$INFO" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes array" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0 dict" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Audio" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Owner" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string public.audio" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions array" "$INFO"
for i in 0 1 2 3 4 5 6; do :; done
idx=0
for ext in mp3 m4a wav aac flac ogg aiff caf; do
  /usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:0:CFBundleTypeExtensions:${idx} string ${ext}" "$INFO"
  idx=$((idx + 1))
done
# Video files (soundtrack is extracted with ffmpeg)
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1 dict" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeName string Video" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeRole string Viewer" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:LSHandlerRank string Alternate" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes array" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes:0 string public.movie" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes:1 string public.mpeg-4" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:LSItemContentTypes:2 string com.apple.quicktime-movie" "$INFO"
/usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeExtensions array" "$INFO"
idx=0
for ext in mp4 mov mkv webm m4v avi mpeg mpg 3gp wmv flv ts mts; do
  /usr/libexec/PlistBuddy -c "Add :CFBundleDocumentTypes:1:CFBundleTypeExtensions:${idx} string ${ext}" "$INFO"
  idx=$((idx + 1))
done

# Version shown in Finder Get Info
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.4.2" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 0.4.2" "$INFO"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion 0.4.2" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 0.4.2" "$INFO"

# Native Python host so the menu bar and process name are 为我闻思, not Python.
VPY="${ASR_HOME}/.venv/bin/python"
if [[ ! -x "$VPY" ]]; then
  echo "Need a virtualenv at $ASR_HOME/.venv before building the app." >&2
  exit 1
fi
PY_INC="$("$VPY" -c "import sysconfig; print(sysconfig.get_path('include'))")"
# Homebrew's Python.framework often lacks the top-level Current symlink, so
# -framework Python fails; link the versioned dylib directly.
PY_LIB="$("$VPY" -c "import sys, pathlib; print(pathlib.Path(sys.base_prefix).resolve() / 'Python')")"
if [[ ! -f "$PY_LIB" ]]; then
  echo "Cannot find the Python library at $PY_LIB" >&2
  exit 1
fi
clang -o "$APP/Contents/MacOS/为我闻思" \
  "$REPO_ROOT/scripts/wensi_launcher.c" \
  -I"$PY_INC" \
  "$PY_LIB"
chmod +x "$APP/Contents/MacOS/为我闻思"
# Binary name is 为我闻思 so the process name matches the menu bar.

# Ad-hoc sign so Launch Services accepts the copied/compiled binaries.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "$APP"
