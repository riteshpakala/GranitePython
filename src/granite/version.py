"""What GranitePython says of itself: its version, and the commit it was built from when that is known."""

from __future__ import annotations

import os
import subprocess

from granite import __version__

VERSION = __version__


def commit() -> str | None:
    """The commit (12 hex) GranitePython was built from: stamped by ``scripts/build-cli.sh`` into a zipapp, or read
    from the checkout it runs from. None when neither says."""
    try:
        from granite import _built  # type: ignore[attr-defined]

        return getattr(_built, "COMMIT", None) or None
    except ImportError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isdir(here):
        return None
    try:
        said = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=here, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    head = said.stdout.strip()
    return head if said.returncode == 0 and head else None


def stamp() -> str:
    """``0.1.0@abcdef012345``, or the version alone: what an installation record's ``granite`` says."""
    made = commit()
    return f"{VERSION}@{made}" if made else VERSION


def compare(a: str, b: str) -> int:
    """``1.2.3`` against ``1.10``: the numbers in order, a missing one a zero."""
    def numbers(text: str) -> list[int]:
        out = []
        for part in text.split("."):
            digits = ""
            for c in part:
                if not c.isdigit():
                    break
                digits += c
            out.append(int(digits) if digits else 0)
            if digits != part:
                break
        return out

    x, y = numbers(a), numbers(b)
    for i in range(max(len(x), len(y))):
        p, q = (x[i] if i < len(x) else 0), (y[i] if i < len(y) else 0)
        if p != q:
            return -1 if p < q else 1
    return 0
