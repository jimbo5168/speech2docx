#!/bin/bash
# Build 语音转文字.app into a destination directory.
set -euo pipefail
ASR_HOME="${ASR_HOME:-$HOME/ASR}"
DEST_DIR="${1:-$ASR_HOME/dist}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="语音转文字.app"
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
/usr/libexec/PlistBuddy -c "Set :CFBundleName 语音转文字" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleName string 语音转文字" "$INFO"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 语音转文字" "$INFO" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 语音转文字" "$INFO"

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

echo "$APP"
