#!/bin/sh
# GranitePython against GraniteRust and GraniteC, natively (scripts/interop.py says what is compared).
#   scripts/interop.sh <GraniteRust checkout> <GraniteC checkout> [--quick]
set -eu
exec "${PYTHON:-python3}" "$(dirname "$0")/interop.py" "$@"
