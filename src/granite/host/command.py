"""A command the installer runs (git): its status, what it wrote to stdout, and all it wrote, stdin /dev/null and an
environment without what must not reach it. GraniteC's command.c."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Sequence

_DROPPED = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES")


@dataclass
class Said:
    status: int
    stdout: str
    output: str


def clean_environment() -> dict[str, str]:
    """This process's environment without Apple's loader paths and a running make's flags."""
    return {k: v for k, v in os.environ.items() if not k.startswith("DYLD_") and k not in _DROPPED}


def run(arguments: Sequence[str], folder: str | None = None) -> Said:
    try:
        done = subprocess.run(list(arguments), cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=clean_environment())
    except FileNotFoundError:
        return Said(127, "", f"{arguments[0]} was not found")
    except OSError as error:
        return Said(127, "", f"{arguments[0]} could not start: {error.strerror}")
    out, err = done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
    return Said(done.returncode if done.returncode >= 0 else 1, out, out + err)


def tail(text: str, lines: int) -> str:
    """The last ``lines`` lines of ``text``, trimmed, as Rust's tail()."""
    kept = text.rstrip("\n").split("\n")[-lines:]
    return "\n".join(kept).strip(" \n\t")
