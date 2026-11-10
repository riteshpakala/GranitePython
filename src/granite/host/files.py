"""Files, as the host side needs them. GraniteC's files.c."""

from __future__ import annotations

import os
import shutil
import tempfile
import time


def read(path: str) -> bytes | None:
    """A file's bytes, or None when it cannot be read."""
    try:
        with open(path, "rb") as opened:
            return opened.read()
    except OSError:
        return None


def write(path: str, data: bytes) -> bool:
    """Writes ``data`` beside ``path`` and moves it into place, so a reader never sees half of it."""
    temporary = f"{path}.{os.getpid()}.writing"
    try:
        with open(temporary, "wb") as opened:
            opened.write(data)
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


def mkdirs(path: str) -> bool:
    try:
        os.makedirs(path, 0o755, exist_ok=True)
    except OSError:
        pass
    return os.path.isdir(path)


def exists(path: str) -> bool:
    return os.path.lexists(path)


def remove_tree(path: str) -> bool:
    """Removes ``path`` and all inside it; True when nothing is left there."""
    if not os.path.lexists(path):
        return True
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
    except OSError:
        return False
    return not os.path.lexists(path)


def stamp_of(path: str) -> tuple[int, float] | None:
    """A file's size and when it was last changed, in seconds; None when it is not there."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns / 1e9


def join(a: str, b: str) -> str:
    """``a/b``, with ``a``'s trailing slashes and ``b``'s leading ones dropped."""
    a = a.rstrip("/") or "/"
    return f"{a}/{b.lstrip('/')}" if a != "/" else f"/{b.lstrip('/')}"


def temporary_folder(what: str) -> str:
    """A new folder in TMPDIR (or /tmp) named for ``what``."""
    base = os.environ.get("TMPDIR") or "/tmp"
    base = base.rstrip("/") or "/"
    return tempfile.mkdtemp(prefix=f"granitepython-{what.replace('/', '-')}-", dir=base)


def now_iso8601() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
