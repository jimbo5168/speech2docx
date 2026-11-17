#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/ASR/dist}"
APP="$DEST/为我闻思 Lite.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"
/usr/bin/swiftc -O "$ROOT/scripts/native_bootstrap.swift" -o "$APP/Contents/MacOS/为我闻思"
cp "$ROOT/docs/release/runtime_manifest.json" "$APP/Contents/Resources/"
for item in src ui templates 佛教词库.txt; do
    ditto "$ROOT/$item" "$APP/Contents/Resources/app/$item"
done
find "$APP/Contents/Resources/app" -name __pycache__ -type d -exec rm -rf {} +
/usr/bin/plutil -create xml1 "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleExecutable -string 为我闻思 "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleIdentifier -string org.speech2docx.lite "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleName -string '为我闻思 Lite' "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundlePackageType -string APPL "$APP/Contents/Info.plist"
/usr/bin/plutil -insert LSMinimumSystemVersion -string 26.0 "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleShortVersionString -string 0.6.1 "$APP/Contents/Info.plist"
/usr/bin/plutil -insert CFBundleVersion -string 0.6.1 "$APP/Contents/Info.plist"
/usr/bin/codesign --force --deep --sign - "$APP"
printf '%s\n' "$APP"
