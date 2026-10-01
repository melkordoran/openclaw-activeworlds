#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'This build script targets Apple Silicon macOS.' >&2
  exit 1
fi
DOTNET_BIN="${DOTNET_BIN:-dotnet}"
if ! command -v "$DOTNET_BIN" >/dev/null 2>&1; then
  echo 'Install .NET SDK 10, or set DOTNET_BIN to its executable path.' >&2
  exit 1
fi
git submodule update --init vendor/axis-sdk
# Upstream uses an SSH URL; use HTTPS without modifying upstream source.
git -C vendor/axis-sdk -c url.https://gitlab.pp16.org/.insteadOf=git@gitlab.pp16.org: submodule update --init --recursive
"$DOTNET_BIN" publish vendor/axis-sdk/Axis.Sdk.LegacyV5 -c Release -r osx-arm64 -p:NativeLib=Shared -o native
python3 bridge.py check
