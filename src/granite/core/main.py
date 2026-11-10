"""The single writer's thread: what the main actor is to Swift Granite, Main to GraniteRust, granite.main to GraniteC.

PIN: A TURN NEVER WAITS. Every write to a run's record, every drain and every settle is one turn on ``granite.main``,
in the order it was sent, so they never interleave. Work that blocks runs on a thread of its own and sends back.

PIN: MAIN IS THE LIBRARY'S. Its turns are GranitePython's own; the author's code never runs on main.

The thread starts on first use and lives as long as the process (a daemon: a runner leaves through ``os._exit``).
"""

from __future__ import annotations

import queue
import sys
import threading
import traceback
from typing import Any, Callable, TypeVar

R = TypeVar("R")

# SimpleQueue: re-entrant, so a finalizer that enqueues while this thread is inside put() cannot deadlock it.
_turns: "queue.SimpleQueue[tuple[Callable[..., Any], tuple[Any, ...]]]" = queue.SimpleQueue()
_started = threading.Lock()
_thread: threading.Thread | None = None


def _loop() -> None:
    while True:
        turn, args = _turns.get()
        try:
            turn(*args)
        except BaseException:  # a turn that raises is a kernel bug: say it, and keep main turning
            sys.stderr.write("granite: a turn on granite.main raised:\n" + traceback.format_exc())
        del turn, args


def _start() -> None:
    global _thread
    if _thread is not None:
        return
    with _started:
        if _thread is None:
            thread = threading.Thread(target=_loop, name="granite.main", daemon=True)
            thread.start()
            _thread = thread


def is_current() -> bool:
    """Whether the calling thread is granite.main."""
    return _thread is not None and threading.current_thread() is _thread


def enqueue(turn: Callable[..., Any], *args: Any) -> None:
    """Runs ``turn(*args)`` on main after everything sent before it. Always deferred, even from main."""
    _start()
    _turns.put((turn, args))


def run(turn: Callable[..., Any], *args: Any) -> None:
    """Runs ``turn(*args)`` now when the caller is main, else on main's next turn (Swift's onMain)."""
    if is_current():
        turn(*args)
    else:
        enqueue(turn, *args)


def call(turn: Callable[..., R], *args: Any) -> R:
    """``turn(*args)`` run on main, its value or its exception handed back: inline when the caller is main."""
    if is_current():
        return turn(*args)
    done = threading.Event()
    outcome: list[Any] = []

    def wrapped() -> None:
        try:
            outcome.append((True, turn(*args)))
        except BaseException as error:  # handed to the caller, raised there
            outcome.append((False, error))
        finally:
            done.set()

    enqueue(wrapped)
    done.wait()
    ok, value = outcome[0]
    if not ok:
        raise value
    return value


def turn() -> None:
    """Returns once main has run every turn sent to it before the call."""
    if is_current():
        raise RuntimeError("granite: main waited for its own turn")
    call(lambda: None)
