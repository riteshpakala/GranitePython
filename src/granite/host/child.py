"""A runner as a child process: started clean, its wire a channel, its stderr's tail kept, and its end (status, CPU,
peak memory) read when it is reaped. GraniteRust's process layer; GraniteC's child.c.

PIN: A RUNNER STARTS CLEAN, WHATEVER ITS HOST BLOCKS OR IGNORES. It leads a process group of its own, starts with an
empty signal mask and default dispositions for TERM, INT, HUP, QUIT and PIPE, and inherits no descriptor past stderr.
``subprocess`` cannot give that (a blocked SIGTERM and an ignored SIGINT pass through it), so libc's ``posix_spawn``
is called by ``ctypes``, as GraniteC calls it, with ``POSIX_SPAWN_CLOEXEC_DEFAULT`` on Apple and every inheritable
descriptor closed elsewhere. Without ``ctypes``, ``os.posix_spawn`` does the same but for a working folder, which a
small Python trampoline then sets.

PIN: A CALL'S PROCESSES GO WITH IT. When the runner is reaped, what is left of its group is killed: a grandchild never
outlives the call.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
import time
from typing import Sequence

from granite.channel import Channel
from granite.host.remote import SPAWN, RemoteError

TAIL_BYTES = 8192
_RESET = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT, signal.SIGPIPE)
_SETPGROUP, _SETSIGDEF, _SETSIGMASK, _CLOEXEC_DEFAULT = 0x02, 0x04, 0x08, 0x4000

#: Tests set this to start runners through ``os.posix_spawn``, the path taken without ``ctypes``.
use_ctypes = True


def status_text(status: int) -> str:
    """``exit N`` or ``signal N``, as Rust's ExitStatus is said."""
    if os.WIFSIGNALED(status):
        return f"signal {os.WTERMSIG(status)}"
    return f"exit {os.WEXITSTATUS(status) if os.WIFEXITED(status) else 0}"


def _os_error(number: int) -> str:
    """Rust's io::Error: the system's sentence and its number."""
    return f"{os.strerror(number)} (os error {number})"


# --- libc's posix_spawn, by ctypes ----------------------------------------------------------------------------------

class _Libc:
    def __init__(self) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        p, i = ctypes.c_void_p, ctypes.c_int
        self.fa_init = self._fn(libc, "posix_spawn_file_actions_init", [p])
        self.fa_dup2 = self._fn(libc, "posix_spawn_file_actions_adddup2", [p, i, i])
        self.fa_close = self._fn(libc, "posix_spawn_file_actions_addclose", [p, i])
        self.fa_destroy = self._fn(libc, "posix_spawn_file_actions_destroy", [p])
        self.fa_chdir = None
        for name in ("posix_spawn_file_actions_addchdir", "posix_spawn_file_actions_addchdir_np"):
            if hasattr(libc, name):
                self.fa_chdir = self._fn(libc, name, [p, ctypes.c_char_p])
                break
        self.attr_init = self._fn(libc, "posix_spawnattr_init", [p])
        self.attr_flags = self._fn(libc, "posix_spawnattr_setflags", [p, ctypes.c_short])
        self.attr_pgroup = self._fn(libc, "posix_spawnattr_setpgroup", [p, i])
        self.attr_sigmask = self._fn(libc, "posix_spawnattr_setsigmask", [p, p])
        self.attr_sigdefault = self._fn(libc, "posix_spawnattr_setsigdefault", [p, p])
        self.attr_destroy = self._fn(libc, "posix_spawnattr_destroy", [p])
        self.sigemptyset = self._fn(libc, "sigemptyset", [p])
        self.sigaddset = self._fn(libc, "sigaddset", [p, i])
        strings = ctypes.POINTER(ctypes.c_char_p)
        self.spawn = self._fn(libc, "posix_spawn", [ctypes.POINTER(i), ctypes.c_char_p, p, p, strings, strings])
        self.spawnp = self._fn(libc, "posix_spawnp", [ctypes.POINTER(i), ctypes.c_char_p, p, p, strings, strings])

    @staticmethod
    def _fn(libc: ctypes.CDLL, name: str, argtypes: list) -> ctypes._CFuncPtr:
        fn = getattr(libc, name)
        fn.argtypes = argtypes
        fn.restype = ctypes.c_int
        return fn


_libc: _Libc | None = None
_libc_tried = False
_libc_lock = threading.Lock()


def _libc_or_none() -> _Libc | None:
    global _libc, _libc_tried
    with _libc_lock:
        if not _libc_tried:
            _libc_tried = True
            try:
                _libc = _Libc()
            except (OSError, AttributeError):
                _libc = None
        return _libc


def _strings(items: Sequence[bytes]) -> ctypes.Array:
    array = (ctypes.c_char_p * (len(items) + 1))()
    array[:-1] = list(items)
    array[-1] = None
    return array


def _inherited(keep: Sequence[int]) -> list[int]:
    """Descriptors past stderr this process would pass on: what a runner must not inherit."""
    try:
        names = os.listdir("/dev/fd")
    except OSError:
        names = [str(fd) for fd in range(3, 256)]
    found = []
    for name in names:
        fd = int(name)
        if fd < 3 or fd in keep:
            continue
        try:
            if os.get_inheritable(fd):
                found.append(fd)
        except OSError:
            pass
    return found


def _spawn_ctypes(libc: _Libc, executable: str, argv: list[bytes], env: list[bytes], directory: str | None,
                  dups: Sequence[tuple[int, int]]) -> int:
    actions = ctypes.create_string_buffer(512)
    attributes = ctypes.create_string_buffer(1024)
    empty = ctypes.create_string_buffer(256)
    defaults = ctypes.create_string_buffer(256)
    libc.fa_init(actions)
    libc.attr_init(attributes)
    try:
        for source, target in dups:
            libc.fa_dup2(actions, source, target)
        if sys.platform != "darwin":
            for fd in _inherited([s for s, _ in dups]):
                libc.fa_close(actions, fd)
        if directory is not None:
            libc.fa_chdir(actions, os.fsencode(directory))
        flags = _SETPGROUP | _SETSIGMASK | _SETSIGDEF | (_CLOEXEC_DEFAULT if sys.platform == "darwin" else 0)
        libc.attr_flags(attributes, flags)
        libc.attr_pgroup(attributes, 0)
        libc.sigemptyset(empty)
        libc.attr_sigmask(attributes, empty)
        libc.sigemptyset(defaults)
        for number in _RESET:
            libc.sigaddset(defaults, int(number))
        libc.attr_sigdefault(attributes, defaults)
        pid = ctypes.c_int(0)
        # A bare name is found on PATH, as Rust's Command finds it; a path is taken as it is.
        call = libc.spawn if "/" in executable else libc.spawnp
        failed = call(ctypes.byref(pid), os.fsencode(executable), actions, attributes, _strings(argv), _strings(env))
        if failed != 0:
            raise OSError(failed, os.strerror(failed))
        return pid.value
    finally:
        libc.fa_destroy(actions)
        libc.attr_destroy(attributes)


_TRAMPOLINE = "import os,sys;os.chdir(sys.argv[1]);os.execvpe(sys.argv[2],sys.argv[2:],os.environ)"


def _spawn_os(executable: str, argv: list[bytes], env: dict[bytes, bytes], directory: str | None,
              dups: Sequence[tuple[int, int]]) -> int:
    actions = [(os.POSIX_SPAWN_DUP2, s, t) for s, t in dups]
    actions += [(os.POSIX_SPAWN_CLOSE, fd) for fd in _inherited([s for s, _ in dups])]
    if directory is not None:
        # No working-folder action: a trampoline sets it and becomes the runner.
        argv = [os.fsencode(sys.executable), b"-I", b"-c", _TRAMPOLINE.encode(), os.fsencode(directory),
                os.fsencode(executable)] + argv[1:]
        executable = sys.executable
    spawn = os.posix_spawn if "/" in executable else os.posix_spawnp
    return spawn(executable, argv, env, file_actions=actions, setpgroup=0, setsigmask=[], setsigdef=_RESET)


def spawn_clean(executable: str, arguments: Sequence[str], directory: str | None, environment: dict[str, str],
                dups: Sequence[tuple[int, int]]) -> int:
    """Starts ``executable`` clean (see the module's pin) and returns its pid. Raises ``OSError``."""
    argv = [os.fsencode(executable)] + [os.fsencode(a) for a in arguments]
    libc = _libc_or_none() if use_ctypes else None
    if libc is not None and (directory is None or libc.fa_chdir is not None):
        env = [os.fsencode(k) + b"=" + os.fsencode(v) for k, v in environment.items()]
        return _spawn_ctypes(libc, executable, argv, env, directory, dups)
    return _spawn_os(executable, argv, {os.fsencode(k): os.fsencode(v) for k, v in environment.items()}, directory,
                     dups)


# --- the child ------------------------------------------------------------------------------------------------------

class Ended:
    """How a child ended: its wait status, its CPU seconds, and its peak resident bytes."""

    __slots__ = ("status", "cpu_seconds", "peak_bytes")

    def __init__(self, status: int, cpu_seconds: float, peak_bytes: int) -> None:
        self.status, self.cpu_seconds, self.peak_bytes = status, cpu_seconds, peak_bytes


def _unreadable(line: bytes, why: str) -> None:
    sys.stderr.write(f"granite-agent: an unreadable line from the runner: {line.decode('utf-8', 'replace')}\n")


class Child:
    """A runner started clean, its stdin and stdout a channel, its stderr's tail kept."""

    def __init__(self, executable: str, arguments: Sequence[str], directory: str | None,
                 environment: dict[str, str]) -> None:
        in_read, in_write = os.pipe()
        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        try:
            self.pid = spawn_clean(executable, arguments, directory, environment,
                                   [(in_read, 0), (out_write, 1), (err_write, 2)])
        except OSError as error:
            for fd in (in_read, in_write, out_read, out_write, err_read, err_write):
                os.close(fd)
            raise RemoteError(SPAWN, "", path=executable, reason=_os_error(error.errno or 0)) from None
        for fd in (in_read, out_write, err_write):
            os.close(fd)
        self.started = time.monotonic()
        self._lock = threading.Condition()
        self._tail = bytearray()
        self._end: Ended | None = None
        self.channel = Channel(out_read, in_write, _unreadable)
        self._tail_thread = threading.Thread(target=self._read_tail, args=(err_read,), name="granite.stderr",
                                             daemon=True)
        self._tail_thread.start()
        threading.Thread(target=self._reap, name="granite.reaper", daemon=True).start()

    def _read_tail(self, fd: int) -> None:
        try:
            while True:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                with self._lock:
                    self._tail += chunk
                    if len(self._tail) > TAIL_BYTES:
                        del self._tail[:-TAIL_BYTES]
        finally:
            os.close(fd)

    def _reap(self) -> None:
        _, status, usage = os.wait4(self.pid, 0)
        # What is left of its group goes with it: a grandchild must not outlive the call.
        try:
            os.kill(-self.pid, 0)
            os.kill(-self.pid, signal.SIGKILL)
        except OSError:
            pass
        peak = usage.ru_maxrss * (1024 if sys.platform.startswith("linux") else 1)
        with self._lock:
            self._end = Ended(status, usage.ru_utime + usage.ru_stime, int(peak))
            self._lock.notify_all()

    @property
    def has_exited(self) -> bool:
        with self._lock:
            return self._end is not None

    def wait(self, seconds: float | None = None) -> Ended | None:
        """How it ended, waiting for it; None when ``seconds`` pass first."""
        with self._lock:
            self._lock.wait_for(lambda: self._end is not None, seconds)
            return self._end

    def terminate(self, grace: float = 2.0) -> None:
        """SIGTERM to its group, then SIGKILL after ``grace`` seconds if it is still there."""
        if self.has_exited:
            return
        try:
            os.kill(-self.pid, signal.SIGTERM)
        except OSError:
            pass

        def kill_after_grace() -> None:
            if self.wait(grace) is None:
                try:
                    os.kill(-self.pid, signal.SIGKILL)
                except OSError:
                    pass

        threading.Thread(target=kill_after_grace, name="granite.kill", daemon=True).start()

    def log(self) -> str:
        """The last six lines of its stderr, trimmed: what a person reads when it ended without answering."""
        if self.has_exited:
            self._tail_thread.join(0.25)  # what it wrote last, read before it is said
        with self._lock:
            tail = bytes(self._tail)
        text = tail.decode("utf-8", "replace").rstrip("\n")
        lines = text.split("\n")
        return "\n".join(lines[-6:]).strip(" \n\t\r")

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started
