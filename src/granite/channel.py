"""Where granite-agent/1 messages travel: a pipe each way, one line per message. GraniteRust's PipeChannel; GraniteC's
channel.c.

PIN: ONE READER, ONE WRITER EACH. A thread reads lines and queues the messages that read; one that does not is told to
``unreadable`` and dropped. A send writes its whole line under the writer's lock, so two threads' lines never
interleave.

``GRANITE_AGENT_TRACE=1`` writes every line read and sent to stderr, as Rust's runner and host do.
"""

from __future__ import annotations

import collections
import os
import sys
import threading
from typing import Callable

from granite import wire

_tracing: bool | None = None
_tracing_lock = threading.Lock()


def trace(arrow: str, line: bytes) -> None:
    """``[granite-agent <pid>] <arrow> <line>`` on stderr, the first 300 bytes, when tracing."""
    global _tracing
    if _tracing is None:
        with _tracing_lock:
            if _tracing is None:
                _tracing = os.environ.get("GRANITE_AGENT_TRACE") == "1"
    if not _tracing:
        return
    shown = line[:300].rstrip(b"\n")
    try:
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    _write_all(2, b"[granite-agent %d] %s %s\n" % (os.getpid(), arrow.encode(), shown))


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


class Channel:
    """Messages over a pipe each way: lines read on a thread of their own, sent whole."""

    def __init__(self, read_fd: int, write_fd: int,
                 unreadable: Callable[[bytes, str], None] | None = None) -> None:
        self._arrived = threading.Condition()
        self._messages: collections.deque[wire.Message] = collections.deque()
        self._ended = False
        self._unreadable = unreadable
        self._writing = threading.Lock()
        self._write_fd = write_fd
        threading.Thread(target=self._read_lines, args=(read_fd,), name="granite.channel", daemon=True).start()

    def _deliver(self, line: bytes) -> None:
        if not line.strip(b" \r"):
            return
        trace("read", line)
        try:
            message = wire.read(line)
        except wire.WireError as error:
            if self._unreadable is not None:
                self._unreadable(line[:200], error.said)
            return
        with self._arrived:
            self._messages.append(message)
            self._arrived.notify()

    def _read_lines(self, fd: int) -> None:
        pending = b""
        try:
            while True:
                try:
                    chunk = os.read(fd, 1 << 16)
                except OSError:
                    break
                if not chunk:
                    break
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    self._deliver(line)
        except BaseException:  # the reader's own fault: the channel ends, and says why
            import traceback
            sys.stderr.write("granite: a channel's reader raised:\n" + traceback.format_exc())
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            with self._arrived:
                self._ended = True
                self._arrived.notify_all()

    def send(self, message: wire.Message) -> str | None:
        """Writes ``message`` as one line; None when it was written, else why not."""
        line = wire.line(message)
        trace("sent", line)
        with self._writing:
            fd = self._write_fd
            if fd < 0:
                return "the channel is closed"
            try:
                _write_all(fd, line)
            except OSError as error:
                return error.strerror or str(error)
        return None

    def recv(self) -> wire.Message | None:
        """The next message, waiting for it; None once the other end has said all it will."""
        with self._arrived:
            while not self._messages and not self._ended:
                self._arrived.wait()
            return self._messages.popleft() if self._messages else None

    def close(self) -> None:
        """Closes the writing end: the other side reads its end."""
        with self._writing:
            fd, self._write_fd = self._write_fd, -1
        if fd >= 0:
            os.close(fd)


def pair() -> tuple[Channel, Channel]:
    """Two channels, each the other's far end, for a host and a runner in one process."""
    there_read, there_write = os.pipe()
    back_read, back_write = os.pipe()
    return Channel(back_read, there_write), Channel(there_read, back_write)
