"""What the host tests share: the fixture runners as executables, as a shelf would hold them."""

import os
import shlex
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
_made: dict[str, str] = {}
_folder = None


def runner(name: str) -> str:
    """``tests/fixtures/<name>.py`` as an executable: a shell line that becomes this Python, isolated."""
    global _folder
    if name in _made:
        return _made[name]
    if _folder is None:
        _folder = tempfile.mkdtemp(prefix="granitepython-runners-")
    path = os.path.join(_folder, name)
    script = FIXTURES / f"{name}.py"
    with open(path, "w") as out:
        out.write(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(script))} "$@"\n')
    os.chmod(path, 0o755)
    _made[name] = path
    return path
