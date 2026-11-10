#!/bin/sh
# GranitePython's suite (scripts/check.sh) on Linux arm64 in Apple's container: Debian's Python 3.11 and the image's
# 3.12, glibc's posix_spawn by ctypes, /proc's descriptors, wait4's units (ru_maxrss is kilobytes there) and /tmp are
# the platform code it proves.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
container=$(command -v container || ls -d "$HOME"/.local/apple-container/*/bin/container 2>/dev/null | tail -1 || true)
[ -n "$container" ] || { echo "Apple's container is not installed: https://github.com/apple/container/releases" >&2; exit 2; }
"$container" system status >/dev/null 2>&1 || "$container" system start
"$container" build --tag granitepython:linux --file "$root/containers/Dockerfile.linux" "$root"
"$container" run --rm --memory "${GRANITE_CHECK_MEMORY:-4g}" --cpus "${GRANITE_CHECK_CPUS:-4}" granitepython:linux
