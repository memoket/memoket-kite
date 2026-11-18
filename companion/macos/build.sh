#!/usr/bin/env bash
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd -P)
if [[ $# != 1 ]]; then
    printf '%s\n' 'Usage: build.sh OUTPUT_DIRECTORY' >&2
    exit 2
fi
mkdir -p "$1"
output=$(cd "$1" && pwd -P)
bundle="$output/KITE Twin.app"
stage=$(mktemp -d "$output/.kite-build.XXXXXXXX")
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/Contents/MacOS"
xcrun swiftc -O -target "$(uname -m)-apple-macosx15.0" -parse-as-library \
    "$here"/Sources/*.swift -o "$stage/Contents/MacOS/KITETwin"
cp "$here/Info.plist" "$stage/Contents/Info.plist"
mkdir -p "$stage/Contents/Resources"
cp "$here/Resources/TwinEnglish.json" "$stage/Contents/Resources/"
codesign --force --sign "${KITE_CODESIGN_IDENTITY:--}" "$stage"
if [[ -e "$bundle" ]]; then
    printf 'Existing app at %s. Build in a new output directory.\n' "$bundle" >&2
    exit 1
fi
mv "$stage" "$bundle"
printf '%s\n' "$bundle"
