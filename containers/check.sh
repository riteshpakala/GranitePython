#!/bin/sh
# Installs a Python agent repository and checks what its runner says against the standard, in an Apple `container`
# (Linux, arm64) or, with --native, on this machine. GraniteRust's containers/check.sh, for Python.
#
#   containers/check.sh <agent folder | git URL[@revision]> [--native] [--granite <GranitePython clone>]
#                       [--out <dir>] [--keep]
#   (GRANITE_CHECK_MEMORY, 4g, and GRANITE_CHECK_CPUS, 4, size each container)
#
# What it checks is containers/check.py's to say.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-python3}
exec "$python" "$here/check.py" "$@"
