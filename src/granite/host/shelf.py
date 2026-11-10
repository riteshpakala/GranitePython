"""A folder of installed runners, one folder each with its ``installation.json``. GraniteRust's AgentShelf; GraniteC's
shelf.c. A shelf written by GraniteRust, GraniteC or GranitePython reads the same in each.

PIN: A SHELF SAYS WHAT DOES NOT READ. A folder whose record does not parse, does not fit, or is of a format this host
does not read is a problem said beside the installations that do; a folder without a record, or one whose name starts
with a dot (an install in progress), is passed over.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

from granite import agentjson
from granite.host import files
from granite.host.installation import FILE, FORMAT, Installation
from granite.host.remote import CHANGED, RemoteError


@dataclass
class ShelfRead:
    installations: list[Installation] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _names_in(folder: str, hidden: bool) -> list[str]:
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(n for n in names if hidden or not n.startswith("."))


def read_all(folder: str) -> ShelfRead:
    read = ShelfRead()
    for name in _names_in(folder, False):
        data = files.read(files.join(files.join(folder, name), FILE))
        if data is None:
            continue
        try:
            installation = Installation.read(agentjson.read(data))
        except (agentjson.JSONError, ValueError) as error:
            read.problems.append(f"{name}: its {FILE} does not read: {error}")
            continue
        if not installation.format.startswith(FORMAT):
            read.problems.append(f"{name}: {installation.format} is not an installation this host reads")
            continue
        read.installations.append(installation)
    return read


def _same_in_any_case(a: str, b: str) -> bool:
    return a.encode("utf-8", "surrogateescape").lower() == b.encode("utf-8", "surrogateescape").lower()


def find(folder: str, name: str) -> Installation | None:
    """The installation named ``name``, or else the one carrying the word ``name``, in any case."""
    installations = read_all(folder).installations
    for installation in installations:
        if _same_in_any_case(installation.name, name):
            return installation
    for installation in installations:
        if any(_same_in_any_case(word, name) for word in installation.words):
            return installation
    return None


def folder_of(folder: str, installation: Installation) -> str:
    return files.join(folder, installation.name)


def executable_of(folder: str, installation: Installation) -> str:
    return files.join(folder_of(folder, installation), installation.executable)


def stamp(folder: str) -> str:
    """Every record's name and when it changed: what a host compares to know the shelf changed."""
    said = []
    for name in _names_in(folder, True):
        stamped = files.stamp_of(files.join(files.join(folder, name), FILE))
        if stamped is not None:
            said.append(f"{name}@{stamped[1]:.6f}")
    return ",".join(said)


def sha256_file(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as opened:
            for chunk in iter(lambda: opened.read(1 << 16), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def verify(folder: str, installation: Installation) -> RemoteError | None:
    """None when the runner on the shelf is the one installed; else why it may not run."""
    path = executable_of(folder, installation)
    stamped = files.stamp_of(path)
    same = False
    if stamped is not None:
        size, modified = stamped
        if size == installation.size and installation.modified is not None and \
                abs(modified - installation.modified) < 0.001:
            same = True
        else:
            same = sha256_file(path) == installation.sha256
    if same:
        return None
    return RemoteError(CHANGED, installation.word, path=path)
