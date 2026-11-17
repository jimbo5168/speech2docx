#!/bin/bash
# Build only inside an explicitly supplied scratch directory.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="${1:?Supply an empty build directory}"
OUTPUT="${2:-$HOME/ASR/dist/release/runtime-macos-arm64-cpython312-r3.tar.gz}"
mkdir -p "$BUILD" "$(dirname "$OUTPUT")"
if [[ -e "$BUILD/python" ]]; then
    echo 'The build directory already contains Python.' >&2
    exit 1
fi
curl -fL --retry 3 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only.tar.gz' -o "$BUILD/python.tar.gz"
PYTHON_SHA="$(/usr/bin/shasum -a 256 "$BUILD/python.tar.gz")"
[[ "${PYTHON_SHA%% *}" == e956e60bc9465d577ef8062059aa05cd3a2665b4b8678a2ed24e432368330b8f ]]
tar -xzf "$BUILD/python.tar.gz" -C "$BUILD"
"$BUILD/python/bin/python3" -m pip install -r "$ROOT/requirements-runtime.txt"
for tool in ffmpeg ffprobe; do
    curl -fL --retry 3 "https://www.osxexperts.net/${tool}902arm.zip" -o "$BUILD/$tool.zip"
    EXPECTED=4597f0f3a397cc0027bfd03dc4dd9c3066be15943cc788370846b4305d178bcb
    if [[ "$tool" == ffprobe ]]; then
        EXPECTED=c765d316eb210f3c98de6f9688df784552f3f8f586090e2a8cb871be9003164e
    fi
    TOOL_SHA="$(/usr/bin/shasum -a 256 "$BUILD/$tool.zip")"
    [[ "${TOOL_SHA%% *}" == "$EXPECTED" ]]
    unzip -o -j "$BUILD/$tool.zip" "$tool" -d "$BUILD/python/bin"
    chmod +x "$BUILD/python/bin/$tool"
done
"$BUILD/python/bin/python3" "$ROOT/scripts/pack_runtime.py" "$BUILD/python" "$OUTPUT"
