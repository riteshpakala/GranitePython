#!/usr/bin/env python3
"""The granite command as one file: GranitePython's package, the commit it was built from, and a __main__ that runs
the CLI, written as the installer writes a runner (sorted, dated once, a .pyc beside each .py).

  scripts/build-cli.py [--out build/granite] [--python <interpreter>]

The first line names ``--python`` (default: ``/usr/bin/env python3``); runners the command installs name the Python
that runs it.
"""

import os
import subprocess
import sys
import tempfile

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "src"))

from granite.host import install  # noqa: E402


def main(argv: list[str]) -> int:
    out, python = os.path.join(root, "build", "granite"), None
    i = 0
    while i < len(argv):
        if argv[i] == "--out" and i + 1 < len(argv):
            out, i = argv[i + 1], i + 2
        elif argv[i] == "--python" and i + 1 < len(argv):
            python, i = argv[i + 1], i + 2
        else:
            sys.stderr.write(__doc__)
            return 2
    commit = os.environ.get("GRANITE_COMMIT")
    if not commit:
        said = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True, text=True)
        commit = said.stdout.strip() if said.returncode == 0 else ""
    entries = dict(install._granite_files())
    entries.pop("granite/_built.py", None)
    entries["granite/_built.py"] = f'"""Stamped by scripts/build-cli.py."""\n\nCOMMIT = {commit or None!r}\n'.encode()
    entries["__main__.py"] = (b'"""The granite command."""\n\nfrom granite.cli import entry\n\n'
                              b'if __name__ == "__main__":\n    entry()\n')
    first = f"#!{python} -I\n" if python else "#!/usr/bin/env python3\n"
    with tempfile.TemporaryDirectory(prefix="granitepython-cli-") as work:
        data = install.zipapp(entries, first.encode(), work)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    temporary = f"{out}.{os.getpid()}.writing"
    with open(temporary, "wb") as written:
        written.write(data)
    os.chmod(temporary, 0o755)
    os.replace(temporary, out)
    print(f"built {out} ({len(data)} bytes, granite {install.version.VERSION}@{commit or 'unknown'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
