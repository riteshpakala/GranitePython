"""A Python agent repository made a runner on this machine: fetched, read, bundled into one file, asked what it
carries, and installed on a shelf whole or not at all. GraniteRust's AgentInstaller; GraniteC's install.c.

A repository says what it is in its ``pyproject.toml``::

    [project]
    dependencies = []

    [tool.granite]
    name = "BlackjackAgent"            # the installation's folder on a shelf
    agent = "blackjack_agent:roster"   # module:attribute of the roster
    granite = "0.1.0"                  # the least GranitePython it needs
    source = "src"                     # the folder whose packages are bundled

PIN: A RUNNER IS ONE FILE. The repository's ``source`` folder and this installer's own ``granite`` package are written
into a zipapp beside a generated ``__main__.py``: sorted names, one fixed date, stored, with a ``.pyc`` beside each
``.py`` (an unchecked hash, so a zip never caches bytecode and a stale one falls back to the source). Its first line
names the Python that ran the installer, in isolated mode (``-I``), so ``PYTHONPATH``, the user's site and the
working folder cannot reach its code. The record's ``sha256`` covers all of it.

PIN: STANDARD LIBRARY ONLY, FOR NOW. A repository that depends on other packages is refused with a sentence: nothing
is fetched from an index, and nothing outside the one file is imported.
"""

from __future__ import annotations

import io
import os
import py_compile
import re
import sys
import time
import tomllib
import zipfile
from dataclasses import dataclass
from importlib import resources
from typing import Callable

from granite import version
from granite.host import command, files, shelf
from granite.host.installation import FILE, Installation
from granite.host.process import Process
from granite.host.remote import RemoteError
from granite.host.source import Source

Progress = Callable[[str, "str | None"], None]

TOOLCHAIN, FETCH, NOT_A_PACKAGE, NOT_GRANITE, PYTHON, DEPENDENCIES, BUILD, NO_AGENT, CLASH, NOT_INSTALLED = (
    "toolchain", "fetch", "not-a-package", "not-granite", "python", "dependencies", "build", "no-agent", "clash",
    "not-installed")

_DATE = (1980, 1, 1, 0, 0, 0)
_FOLDER_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")
_AGENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):([A-Za-z_][A-Za-z0-9_]*)\Z")


class InstallError(Exception):
    """Why an install did not happen, and the sentence a person reads (``str``)."""

    def __init__(self, kind: str, *, source: str = "", reason: str = "", name: str = "", log: str = "",
                 path: str = "", word: str = "", installed: str = "") -> None:
        self.kind, self.source, self.reason, self.name = kind, source, reason, name
        self.log, self.path, self.word, self.installed = log, path, word, installed
        super().__init__(self.text)

    @property
    def text(self) -> str:
        k = self.kind
        if k == TOOLCHAIN:
            return f"a Python a runner can name is needed to build an agent: {self.reason}"
        if k == FETCH:
            return f"could not fetch {self.source}: {self.reason}"
        if k == NOT_A_PACKAGE:
            return f"{self.source} is not a Python agent repository: {self.reason}"
        if k == NOT_GRANITE:
            return f"{self.name} does not build against GranitePython: {self.reason}"
        if k == PYTHON:
            return f"{self.name} does not run on this Python: {self.reason}"
        if k == DEPENDENCIES:
            return f"{self.name} depends on {self.reason}, which a Python runner does not bundle yet: it carries the " \
                   "standard library and GranitePython only"
        if k == BUILD:
            return f"{self.name} did not build (the whole log is at {self.path}):\n{self.log}"
        if k == NO_AGENT:
            return f"{self.name} holds no agent: its runner describes none"
        if k == CLASH:
            return f"an agent called `{self.word}` is already installed, from {self.installed}"
        if k == NOT_INSTALLED:
            return f"no agent installed here is called {self.name}"
        return ""

    def __str__(self) -> str:
        return self.text


# --- the repository -------------------------------------------------------------------------------------------------

@dataclass
class Manifest:
    name: str
    module: str
    attribute: str
    granite: str | None
    source: str
    requires_python: str | None
    dependencies: list[str]


def _hint(package: str) -> str:
    for file, who in (("Cargo.toml", "GraniteRust's granite installs Rust agents"),
                      ("granite-agent.json", "GraniteC's granite installs C agents"),
                      ("Package.swift", "Craft installs Swift agents")):
        if os.path.exists(os.path.join(package, file)):
            return f" (it has a {file}: {who})"
    return ""


def read_manifest(package: str) -> Manifest:
    """``pyproject.toml``'s ``[project]`` and ``[tool.granite]``; raises ``ValueError`` saying why the repository is not
    one."""
    data = files.read(os.path.join(package, "pyproject.toml"))
    if data is None:
        raise ValueError(f"it has no pyproject.toml{_hint(package)}")
    try:
        toml = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"its pyproject.toml does not read: {error}") from None
    tool = toml.get("tool", {}).get("granite") if isinstance(toml.get("tool"), dict) else None
    if not isinstance(tool, dict):
        raise ValueError(f"its pyproject.toml has no [tool.granite]{_hint(package)}")
    name, agent = tool.get("name"), tool.get("agent")
    if not isinstance(name, str):
        raise ValueError("its [tool.granite] names no `name`")
    if not _FOLDER_NAME.match(name):
        raise ValueError(f"`name` is not a folder name: {name}")
    if not isinstance(agent, str):
        raise ValueError("its [tool.granite] names no `agent`: module:attribute of its roster")
    matched = _AGENT.match(agent)
    if not matched:
        raise ValueError(f"`agent` is not module:attribute: {agent}")
    module = matched.group(1)
    if module.split(".")[0] == "granite":
        raise ValueError("its `agent` is in a package named granite, which would hide GranitePython's own")
    granite = tool.get("granite")
    source = tool.get("source", "src")
    if not isinstance(source, str) or source.startswith("/") or ".." in source.split("/"):
        raise ValueError(f"`source` is not a folder inside the repository: {source}")
    if not os.path.isdir(os.path.join(package, source)):
        raise ValueError(f"its `source` folder {source} is not there")
    top = module.split(".")[0]
    root = os.path.join(package, source)
    if not (os.path.isfile(os.path.join(root, top, "__init__.py")) or os.path.isfile(os.path.join(root, f"{top}.py"))):
        raise ValueError(f"its `agent` is in {top}, which is not in {source}")
    project = toml.get("project", {}) if isinstance(toml.get("project"), dict) else {}
    dependencies = project.get("dependencies", [])
    requires = project.get("requires-python")
    return Manifest(name, module, matched.group(2), granite if isinstance(granite, str) else None, source,
                    requires if isinstance(requires, str) else None,
                    [d for d in dependencies if isinstance(d, str)] if isinstance(dependencies, list) else [])


def python_fits(spec: str, running: tuple[int, ...]) -> bool:
    """Whether ``running`` keeps ``spec`` (``>=3.11``, ``>=3.11,<4``, ``~=3.11``, ``==3.12.*``); one this reader does not
    understand is kept."""
    def parts(text: str) -> tuple[int, ...]:
        return tuple(int(p) for p in text.split("."))

    for clause in spec.split(","):
        clause = clause.strip()
        matched = re.fullmatch(r"(~=|==|!=|<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)*)(\.\*)?", clause)
        if not matched:
            continue
        op, wanted, star = matched.group(1), parts(matched.group(2)), matched.group(3)
        have = running[: len(wanted)] if star or op == "~=" else running[:3]
        if op == "~=":
            if not (running[:3] >= wanted and running[: len(wanted) - 1] == wanted[:-1]):
                return False
            continue
        padded = wanted + (0,) * (len(have) - len(wanted)) if not star else wanted
        checks = {"==": have == padded, "!=": have != padded, "<=": have <= padded, ">=": have >= padded,
                  "<": have < padded, ">": have > padded}
        if not checks[op]:
            return False
    return True


# --- the bundle -----------------------------------------------------------------------------------------------------

def _source_files(root: str) -> list[tuple[str, bytes]]:
    found = []
    for folder, folders, names in os.walk(root):
        folders[:] = sorted(d for d in folders if not d.startswith(".") and d != "__pycache__")
        for name in names:
            if name.startswith(".") or name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(folder, name)
            with open(path, "rb") as opened:
                found.append((os.path.relpath(path, root).replace(os.sep, "/"), opened.read()))
    return found


def _granite_files() -> list[tuple[str, bytes]]:
    """This installer's own ``granite`` package, from a folder or from the zipapp it runs in."""
    found = []

    def walk(node, prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            if child.is_dir():
                if child.name != "__pycache__":
                    walk(child, f"{prefix}{child.name}/")
            elif child.name.endswith(".py"):
                found.append((f"{prefix}{child.name}", child.read_bytes()))

    walk(resources.files("granite"), "granite/")
    return found


def _main(name: str, manifest: Manifest) -> bytes:
    return (f'"""The runner of {name}, made by GranitePython {version.VERSION}: {manifest.module}:'
            f'{manifest.attribute}."""\n\n'
            "import importlib\n\nimport granite\n\n"
            'if __name__ == "__main__":\n'
            f"    granite.serve(getattr(importlib.import_module({manifest.module!r}), {manifest.attribute!r}))\n"
            ).encode()


def _compiled(name: str, data: bytes, work: str) -> bytes:
    """The unchecked-hash ``.pyc`` of ``name``; raises ``py_compile.PyCompileError``."""
    source = os.path.join(work, "compiling.py")
    target = os.path.join(work, "compiling.pyc")
    with open(source, "wb") as out:
        out.write(data)
    py_compile.compile(source, cfile=target, dfile=name, doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    with open(target, "rb") as opened:
        return opened.read()


def interpreter() -> str:
    """The Python a runner's first line names: this one, as it was started. Raises ``ValueError`` saying why it cannot
    be named."""
    path = sys.executable
    if not path:
        raise ValueError("this Python does not know its own path")
    if any(c in path for c in " \t\n"):
        raise ValueError(f"its path has a space, which a script's first line cannot hold: {path}")
    if len(path) > 200:
        raise ValueError(f"its path is longer than a script's first line holds: {path}")
    return path


def bundle(package: str, manifest: Manifest, work: str) -> bytes:
    """The runner's bytes: a first line naming this Python, then the zip. Raises ``py_compile.PyCompileError``."""
    entries: dict[str, bytes] = {}
    for name, data in _granite_files() + _source_files(os.path.join(package, manifest.source)):
        entries.setdefault(name, data)  # GranitePython's own come first: an agent cannot replace them
    entries["__main__.py"] = _main(manifest.name, manifest)
    return zipapp(entries, b"#!" + os.fsencode(interpreter()) + b" -I\n", work)


def zipapp(entries: dict[str, bytes], first_line: bytes, work: str) -> bytes:
    """``first_line`` then a zip of ``entries``, a ``.pyc`` beside each ``.py``: sorted, dated once, stored, so the same
    entries give the same bytes. Raises ``py_compile.PyCompileError``."""
    entries = dict(entries)
    for name in [n for n in entries if n.endswith(".py")]:
        entries[name[:-3] + ".pyc"] = _compiled(name, entries[name], work)
    out = io.BytesIO()
    out.write(first_line)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, _DATE)
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, entries[name])
    return out.getvalue()


# --- installing -----------------------------------------------------------------------------------------------------

def _stage(progress: Progress | None, said: str) -> None:
    if progress is not None:
        progress(said, None)


def _head_of(folder: str) -> str | None:
    said = command.run(["git", "rev-parse", "HEAD"], folder)
    head = said.stdout.strip()
    return head if said.status == 0 and head else None


def _clone_name(url: str) -> str:
    end = url.rstrip("/")
    start = max(end.rfind("/"), end.rfind(":")) + 1
    name = end[start:]
    return (name[:-4] if name.endswith(".git") else name) or "package"


def install(folder: str, source: Source, progress: Progress | None = None) -> Installation:
    """Installs ``source`` on the shelf ``folder``; the record it wrote. Raises ``InstallError``."""
    said_source = source.text
    builds = files.join(folder, ".builds")
    work = files.join(builds, f"{time.time_ns():x}")
    if not files.mkdirs(work):
        raise InstallError(FETCH, source=said_source, reason=f"could not make {work}")
    try:
        return _install(folder, source, said_source, builds, work, progress)
    finally:
        files.remove_tree(work)


def _install(folder: str, source: Source, said_source: str, builds: str, work: str,
             progress: Progress | None) -> Installation:
    # 1. Fetch.
    if source.url is not None:
        _stage(progress, f"fetching {source.url}")
        package = files.join(work, _clone_name(source.url))
        said = command.run(["git", "clone", "--quiet", source.url, package], work)
        if said.status != 0:
            raise InstallError(FETCH, source=source.url, reason=command.tail(said.output, 4))
        if source.revision is not None:
            said = command.run(["git", "checkout", "--quiet", source.revision], package)
            if said.status != 0:
                raise InstallError(FETCH, source=f"{source.url}@{source.revision}",
                                   reason=command.tail(said.output, 4))
    else:
        package = source.path or ""
        if not os.path.isdir(package):
            raise InstallError(NOT_A_PACKAGE, source=said_source, reason="it is not a folder")
    revision = _head_of(package)

    # 2. Read the repository.
    _stage(progress, "reading the package")
    try:
        manifest = read_manifest(package)
    except ValueError as error:
        raise InstallError(NOT_A_PACKAGE, source=said_source, reason=str(error)) from None
    name = manifest.name
    if manifest.granite and version.compare(version.VERSION, manifest.granite) < 0:
        raise InstallError(NOT_GRANITE, name=name,
                           reason=f"it needs GranitePython {manifest.granite}, and this is {version.VERSION}")
    running = tuple(sys.version_info[:3])
    if manifest.requires_python and not python_fits(manifest.requires_python, running):
        raise InstallError(PYTHON, name=name, reason=f"it needs Python {manifest.requires_python}, and this is "
                                                     f"{'.'.join(map(str, running))}")
    if manifest.dependencies:
        raise InstallError(DEPENDENCIES, name=name, reason=", ".join(manifest.dependencies))
    try:
        interpreter()
    except ValueError as error:
        raise InstallError(TOOLCHAIN, reason=str(error)) from None

    # 3. Build: one file, the runner.
    _stage(progress, f"building {name}")
    built = files.join(work, "agent")
    try:
        runner = bundle(package, manifest, work)
    except py_compile.PyCompileError as error:
        log = files.join(builds, f"{name}.log")
        files.write(log, str(error.msg).encode())
        raise InstallError(BUILD, name=name, log=command.tail(str(error.msg), 12), path=log) from None
    if not files.write(built, runner):
        raise InstallError(FETCH, source=said_source, reason=f"could not write {built}")
    os.chmod(built, 0o755)

    # 4. Ask it what it carries, and check no other installation carries the same.
    _stage(progress, f"asking {name} what it carries")
    try:
        entries = Process(built).describe()
    except RemoteError as error:
        raise InstallError(NO_AGENT, name=f"{name} ({error})") from None
    if not entries:
        raise InstallError(NO_AGENT, name=name)
    words = [e.get("word") for e in entries if isinstance(e, dict) and type(e.get("word")) is str]
    for other in shelf.read_all(folder).installations:
        if other.name == name:
            continue
        for word in words:
            if word in other.words:
                raise InstallError(CLASH, word=word, installed=other.source.text)

    # 5. Install, whole or not at all.
    _stage(progress, f"installing {name}")
    staged = files.join(folder, f".{name}.new")
    files.remove_tree(staged)
    destination = files.join(folder, name)
    executable = files.join(staged, "agent")
    record = None
    ok = files.mkdirs(staged) and files.write(executable, runner)
    if ok:
        os.chmod(executable, 0o755)
        size, modified = files.stamp_of(executable) or (0, 0.0)
        record = Installation(name=name, source=source, executable="agent", sha256=shelf.sha256_file(executable) or "",
                              size=size, built_at=files.now_iso8601(), entries=entries, revision=revision,
                              granite=version.stamp(), modified=modified)
        from granite import agentjson
        ok = files.write(files.join(staged, FILE), agentjson.write_pretty(record.json()))
    if ok:
        files.remove_tree(destination)
        try:
            os.rename(staged, destination)
        except OSError:
            ok = False
    if not ok or record is None:
        files.remove_tree(staged)
        raise InstallError(FETCH, source=said_source, reason="could not install: the shelf could not be written")
    _stage(progress, f"installed {name}: {', '.join(words)}")
    return record


def update(folder: str, name: str, progress: Progress | None = None) -> Installation:
    """Installs the newest of what ``name`` was installed from."""
    found = shelf.find(folder, name)
    if found is None:
        raise InstallError(NOT_INSTALLED, name=name)
    return install(folder, found.source.newest(), progress)


def remove(folder: str, name: str) -> Installation:
    """Removes the installation ``name`` (or the one carrying the word); the record it had."""
    found = shelf.find(folder, name)
    if found is None:
        raise InstallError(NOT_INSTALLED, name=name)
    if not files.remove_tree(shelf.folder_of(folder, found)):
        raise InstallError(FETCH, source=found.name, reason="it could not be removed")
    return found

