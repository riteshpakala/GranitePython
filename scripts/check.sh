#!/bin/sh
# The repository's one check, on every Python it finds of those GranitePython supports (GRANITE_PYTHONS, default
# python3.11 and python3.12): every file compiles with warnings as errors, and every test passes.
set -eu
cd "$(dirname "$0")/.."
ran=0
for python in ${GRANITE_PYTHONS:-python3.11 python3.12}; do
  if ! command -v "$python" >/dev/null 2>&1; then
    echo "granite: $python is not here; skipped"
    continue
  fi
  echo "== $python ($("$python" -c 'import sys; print(sys.version.split()[0])'))"
  "$python" -W error -m compileall -q src tests scripts
  PYTHONPATH=src "$python" -W error -m unittest discover -s tests -t . ${GRANITE_TEST_VERBOSE:+-v}
  ran=$((ran + 1))
done
[ "$ran" -gt 0 ] || { echo "granite: no Python 3.11 or 3.12 found"; exit 1; }
echo "granite: check passed on $ran Python(s)"
