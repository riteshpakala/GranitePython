"""The granite command: GraniteRust's granite-cli, its usage, outputs and exit codes byte for byte. GraniteC's
granite.c.

    granite agents add <name | git URL[@revision] | folder> --shelf <dir>
    granite agents update <name> --shelf <dir>
    granite agents rm <name> --shelf <dir>
    granite agents list --shelf <dir> [--json]
    granite describe <runner>
    granite run <runner> <perform.json | -> [--directory <dir>] [--ask <text>] [--word <word>]
    granite check --schemas <dir>... --schema <file or $id> <instance.json | ->

PIN: NO DEFAULT SHELF. A shelf is ``--shelf`` or ``GRANITE_SHELF``, never a host's own folder by accident.

PIN: ``run`` IS A HOST WITH NO MODEL. It prints one line, ``{"output": …}`` or ``{"failed": …, "refused": …}``, with
the process's status, and exits 0 answered, 3 refused, 1 failed, 2 when the runner broke the wire or died. SIGINT or
SIGTERM during a run cancels the runner and reaps it, then ends this process by the same signal.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Sequence

from granite import agentjson, wire
from granite.context import CancelFlag, Host
from granite.error import Fault, Refusal
from granite.host import install, shelf
from granite.host.process import Process
from granite.host.remote import FAILED, FAULT, REFUSED, RemoteError
from granite.host.source import Source
from granite.jsonschema import Schemas, validate

USAGE = (
    "usage:\n"
    "  granite agents add <name | git URL[@revision] | folder> --shelf <dir>\n"
    "  granite agents update <name> --shelf <dir>\n"
    "  granite agents rm <name> --shelf <dir>\n"
    "  granite agents list --shelf <dir> [--json]\n"
    "  granite describe <runner>\n"
    "  granite run <runner> <perform.json | -> [--directory <dir>] [--ask <text>] [--word <word>]\n"
    "  granite check --schemas <dir>... --schema <file or $id> <instance.json | ->"
)


class _Arguments:
    def __init__(self) -> None:
        self.words: list[str] = []
        self.options: list[tuple[str, str]] = []
        self.json = False

    def one(self, name: str) -> str | None:
        """The last value of ``--name``."""
        found = None
        for key, value in self.options:
            if key == name:
                found = value
        return found

    def all(self, name: str) -> list[str]:
        return [value for key, value in self.options if key == name]


def _err(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def _fail(code: int, said: str) -> int:
    _err(f"granite: {said}\n")
    return code


def _os_error(error: OSError) -> str:
    number = error.errno or 0
    return f"{os.strerror(number)} (os error {number})"


def _shelf(a: _Arguments) -> str | None:
    return a.one("shelf") or os.environ.get("GRANITE_SHELF") or None


def _print_pretty(value) -> None:
    sys.stdout.buffer.write(agentjson.write_pretty(value) + b"\n")
    sys.stdout.flush()


def _progress(stage: str, line: str | None) -> None:
    _err(f"  {line}\n" if line is not None else f"{stage}\n")


# --- agents ---------------------------------------------------------------------------------------------------------

def _add(a: _Arguments, said_source: str) -> int:
    folder = _shelf(a)
    if folder is None:
        return _fail(2, "say which shelf: --shelf <dir> or GRANITE_SHELF")
    try:
        here = os.getcwd()
    except OSError:
        here = "/"
    try:
        installed = install.install(folder, Source.parse(said_source, here), _progress)
    except install.InstallError as error:
        return _fail(1, str(error))
    _print_pretty(installed.json())
    return 0


def _update(a: _Arguments, name: str) -> int:
    folder = _shelf(a)
    if folder is None:
        return _fail(2, "say which shelf: --shelf <dir> or GRANITE_SHELF")
    try:
        installed = install.update(folder, name, _progress)
    except install.InstallError as error:
        return _fail(1, str(error))
    _err(f"updated {installed.name} at {installed.revision or 'its folder'}\n")
    return 0


def _remove(a: _Arguments, name: str) -> int:
    folder = _shelf(a)
    if folder is None:
        return _fail(2, "say which shelf: --shelf <dir> or GRANITE_SHELF")
    try:
        removed = install.remove(folder, name)
    except install.InstallError as error:
        return _fail(1, str(error))
    _err(f"removed {removed.name}\n")
    return 0


def _list(a: _Arguments) -> int:
    folder = _shelf(a)
    if folder is None:
        return _fail(2, "say which shelf: --shelf <dir> or GRANITE_SHELF")
    read = shelf.read_all(folder)
    if a.json:
        _print_pretty([installation.json() for installation in read.installations])
        return 0
    lines = []
    for installation in read.installations:
        at = installation.revision[:7] if installation.revision else "folder"
        for entry in installation.entries:
            entry = entry if isinstance(entry, dict) else {}
            word = entry.get("word") if type(entry.get("word")) is str else ""
            summary = entry.get("summary") if type(entry.get("summary")) is str else ""
            lines.append(f"{word:<12} {installation.name}@{at} · {summary}\n")
    sys.stdout.write("".join(lines))
    sys.stdout.flush()
    for problem in read.problems:
        _err(f"{problem}\n")
    return 0


# --- describe and run -----------------------------------------------------------------------------------------------

def _describe(runner: str) -> int:
    try:
        entries = Process(runner).describe()
    except RemoteError as error:
        return _fail(2, str(error))
    _print_pretty(entries)
    return 0


def _read_input(said: str) -> bytes:
    """``-`` (stdin) or a file's bytes; raises ``ValueError`` with Rust's sentence for why not."""
    if said == "-":
        return sys.stdin.buffer.read()
    try:
        with open(said, "rb") as opened:
            return opened.read()
    except OSError as error:
        raise ValueError(f"{said}: {_os_error(error)}") from None


def _run(a: _Arguments, runner: str, perform: str) -> int:
    try:
        data = _read_input(perform)
    except ValueError as error:
        return _fail(2, str(error))
    try:
        said = agentjson.read(data)
    except agentjson.JSONError as error:
        return _fail(2, f"{perform}: {error}")
    call = said["perform"] if isinstance(said, dict) and "perform" in said else said
    call = call if isinstance(call, dict) else {}

    def string(key: str) -> str | None:
        value = call.get(key)
        return value if type(value) is str else None

    word = a.one("word") or string("word")
    if word is None:
        return _fail(2, "say which agent: a perform's `word`, or --word")
    input_bytes = agentjson.write(call["input"]) if "input" in call else b"{}"
    directory = a.one("directory") or string("directory")
    if directory is not None and not directory.startswith("/"):
        try:
            directory = f"{os.getcwd()}/{directory}"
        except OSError:
            pass
    ask = a.one("ask") or string("ask")
    host = Host(directory, ask, None, lambda line: _err(f"note: {line}\n"))

    # SIGINT and SIGTERM during a run: the runner is cancelled and reaped, then this process ends by the same signal.
    cancel, interrupted = CancelFlag(), []
    if threading.current_thread() is threading.main_thread():
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)

        def record(number: int, frame) -> None:
            interrupted.append(number)

        signal.signal(signal.SIGINT, record)
        signal.signal(signal.SIGTERM, record)
        signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)

        def watch() -> None:
            try:
                os.read(read_fd, 1)
            except OSError:
                return
            cancel.set()

        threading.Thread(target=watch, name="granite.interrupt", daemon=True).start()

    cost = None
    failed: RemoteError | None = None
    output = b""
    try:
        output, cost = Process(runner).perform(word, input_bytes, host, cancel)
    except RemoteError as error:
        failed, cost = error, error.cost
    if cost is not None:
        _err(f"{cost.sentence()}\n")
    if interrupted:
        # Cancelled and reaped: end as the signal would have ended us.
        signal.signal(interrupted[0], signal.SIG_DFL)
        os.kill(os.getpid(), interrupted[0])

    if failed is None:
        line: wire.Message = wire.Output(agentjson.read(output))
        code = 0
    elif failed.kind == REFUSED and failed.refusal is not None:
        line = wire.Failed(Refusal(failed.refusal.kind, reasons=failed.refusal.reasons, said=failed.message or ""))
        code = 3
    elif failed.kind in (FAULT, FAILED):
        line = wire.Failed(Fault(failed.message or ""))
        code = 1
    else:
        line = wire.Failed(Fault(str(failed)))
        code = 2
    printed = agentjson.read(wire.line(line))
    printed["status"] = cost.status if cost is not None else ""
    sys.stdout.buffer.write(agentjson.write(printed) + b"\n")
    sys.stdout.flush()
    return code


# --- check ----------------------------------------------------------------------------------------------------------

def _check(a: _Arguments, instance: str) -> int:
    name = a.one("schema")
    if name is None:
        return _fail(2, "say which schema: --schema <file or $id>")
    schemas = Schemas()
    for folder in a.all("schemas"):
        try:
            schemas.add_folder(folder)
        except OSError as error:
            return _fail(2, f"{folder}: {_os_error(error)}")
    schema = schemas.find(name)
    if schema is None:
        try:
            with open(name, "rb") as opened:
                text = opened.read()
        except OSError as error:
            return _fail(2, f"{name}: {_os_error(error)}")
        try:
            schema = agentjson.read(text)
        except agentjson.JSONError as error:
            return _fail(2, f"{name}: {error}")
    try:
        data = _read_input(instance)
    except ValueError as error:
        return _fail(2, str(error))
    try:
        value = agentjson.read(data)
    except agentjson.JSONError as error:
        return _fail(2, f"{instance}: {error}")
    problems = validate(schemas, schema, value)
    if not problems:
        _err(f"valid: {instance} against {name}\n")
        return 0
    _err("".join(f"{p}\n" for p in problems))
    return _fail(1, f"{instance} does not keep {name}: {len(problems)} problems")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    a = _Arguments()
    i = 0
    while i < len(arguments):
        argument = arguments[i]
        if argument.startswith("--"):
            flag = argument[2:]
            if flag in ("json", "yes"):
                a.json = a.json or flag == "json"
                i += 1
                continue
            if i + 1 >= len(arguments):
                _err(f"--{flag} needs a value\n{USAGE}\n")
                return 2
            a.options.append((flag, arguments[i + 1]))
            i += 2
            continue
        a.words.append(argument)
        i += 1
    w, n = a.words, len(a.words)
    if n == 3 and w[:2] == ["agents", "add"]:
        return _add(a, w[2])
    if n == 3 and w[:2] == ["agents", "update"]:
        return _update(a, w[2])
    if n == 3 and w[:2] == ["agents", "rm"]:
        return _remove(a, w[2])
    if (n == 2 and w == ["agents", "list"]) or (n == 1 and w == ["agents"]):
        return _list(a)
    if n == 2 and w[0] == "describe":
        return _describe(w[1])
    if n == 3 and w[0] == "run":
        return _run(a, w[1], w[2])
    if n == 2 and w[0] == "check":
        return _check(a, w[1])
    return _fail(2, USAGE)


def entry() -> None:
    """The console script: ``main``'s code as the exit."""
    sys.exit(main())
