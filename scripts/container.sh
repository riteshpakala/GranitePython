#!/bin/sh
# The granite command in the check image, with this folder mounted at /scratch: what a host running Python agents in
# a container runs. `scripts/container.sh describe /scratch/shelf/BlackjackAgent/agent`, for one. With -i, stdin
# stays open (for a game played through `granite run`).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
container=$(command -v container || ls -d "$HOME"/.local/apple-container/*/bin/container 2>/dev/null | tail -1 || true)
[ -n "$container" ] || { echo "Apple's container is not installed: https://github.com/apple/container/releases" >&2; exit 2; }
"$container" system status >/dev/null 2>&1 || "$container" system start
commit=$(git -C "$root" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
"$container" build --tag granitepython:check --build-arg GRANITE_COMMIT="$commit" --file "$root/containers/Dockerfile" "$root" >&2
interactive=""
if [ "${1:-}" = "-i" ]; then interactive="--interactive"; shift; fi
exec "$container" run --rm $interactive --volume "$(pwd):/scratch" granitepython:check "$@"
