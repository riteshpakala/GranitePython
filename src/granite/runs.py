"""The engine behind a package's calls: GraniteRust's RunContainer (Swift's AgentRunContainer), with the one policy
package calls need, parallel up to the package's lanes, since every call has a key of its own. GraniteC's runs.h.

PIN: EVERY CHANGE IS MAIN'S. Starting, cancelling, draining and settling are turns on granite.main, as a Granite send
is; a run's thread only ever enqueues.

PIN: A WAIT SETTLES AFTER EVERYTHING IS SAID. A finished run's waiter resumes in the drain that read its end, after
its last event reached the taps; a cancelled one's in the cancel's turn.

PIN: A CANCEL IS SAID AT ONCE. The run's flag is set, its waiter settles cancelled, and whatever its thread enqueues
after is dropped.

PIN: THE RUN'S OWN ERROR IS KEPT. What the run raised is handed to its waiter as it was, so a refusal keeps its kind;
anything that is not a GraniteError is a fault said as it is.
"""

from __future__ import annotations

import sys
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple

from granite.context import CancelFlag, Context, Host
from granite.core import locked, main
from granite.core.emitter import Emitter
from granite.core.locked import Locked
from granite.error import Cancelled, Fault, GraniteError, fault_of

#: Makes the compute every run shares, on a run's thread, blocking (the package's ``compute``).
Compute = Callable[[], Any]
#: The run, on its own thread, blocking (the package's ``run``): ``body(context, job)``.
Body = Callable[[Context, Any], None]


class RunEvent(NamedTuple):
    """One event a tap hears: whose run it is, and the event."""

    key: str
    event: Any


@dataclass
class RunsState:
    """What the engine counted, as GraniteRust's PackageState."""

    runs: int = 0
    finished: int = 0
    cancelled: int = 0
    failed: int = 0
    events: int = 0
    last_failure: str | None = None


class _Waiter:
    __slots__ = ("settled", "result")

    def __init__(self) -> None:
        self.settled = threading.Event()
        self.result: GraniteError | None = None


class _Run:
    """One run, live or waiting for a lane."""

    __slots__ = ("key", "raw", "cancel", "host", "requested", "job", "waiter")

    def __init__(self, key: str, host: Host, requested: CancelFlag | None, job: Any) -> None:
        self.key = key
        self.raw = ""
        self.cancel = CancelFlag()
        self.host = host
        self.requested = requested
        self.job = job
        self.waiter: _Waiter | None = _Waiter()  # main-only: whoever performs it, until it settles


class _Frame:
    """What a run's thread hands main: an event, or how it ended (``ended``; ``error`` None when it finished)."""

    __slots__ = ("run", "ended", "event", "cancelled", "error")

    def __init__(self, run: _Run, ended: bool, event: Any = None) -> None:
        self.run = run
        self.ended = ended
        self.event = event
        self.cancelled = False
        self.error: GraniteError | None = None


class _Inbox:
    __slots__ = ("frames", "scheduled")

    def __init__(self) -> None:
        self.frames: list[_Frame] = []
        self.scheduled = False


class Runs:
    """An engine for ``word``'s calls: at most ``lanes`` at once (below 1: no bound), runs named ``{label}-{n}``."""

    def __init__(self, word: str, body: Body, compute: Compute | None = None, lanes: int = 0,
                 label: str = "PackageRun") -> None:
        self.word = word
        self.label = label
        self.lanes = lanes
        self._body = body
        self._compute = compute
        self._taps: Emitter[RunEvent] = Emitter("a run engine's taps")
        # main-only
        self._live: list[_Run] = []
        self._waiting: list[_Run] = []
        self._counter = 0
        self._state = RunsState()
        # the inbox: any thread in, main out
        self._inbox: Locked[_Inbox] = Locked(_Inbox(), "a run engine's inbox")

    def tap(self, receiver: Any, handler: Callable[[Any, RunEvent], None]) -> None:
        """Every event of every run, as it is drained, on main, while ``receiver`` lives."""
        self._taps.on(receiver, handler)

    # --- performing and cancelling ------------------------------------------------------------------------------

    def perform(self, key: str, host: Host | None = None, requested: CancelFlag | None = None, job: Any = None) -> None:
        """Starts a run keyed ``key`` (or queues it for a lane), offered ``host``, with ``job``, and blocks until it
        settles: returns when it finished, else raises what it ended with (a ``Refusal``, a ``Fault``, ``Cancelled``).
        A run whose ``requested`` flag is set by the turn that would start it settles cancelled without starting; one
        set later is the caller's to pass on with ``cancel``. Never call it from main."""
        if main.is_current():
            raise Fault("a run was performed from main, which would wait on itself")
        run = _Run(key, host or Host.none(), requested, job)
        waiter = run.waiter
        assert waiter is not None
        # One main turn starts the run and opens its wait, so nothing settles in between.
        main.call(self._start_turn, run)
        del run
        waiter.settled.wait()
        if waiter.result is not None:
            raise waiter.result

    def cancel(self, key: str) -> None:
        """Cancels the run keyed ``key``, live or waiting for a lane, from any thread."""
        main.run(self._cancel_turn, key)

    def state(self) -> RunsState:
        """What the engine has counted, read on main."""
        return main.call(lambda: RunsState(**vars(self._state)))

    def count(self) -> tuple[int, int]:
        """How many runs are live and how many wait for a lane, read on main."""
        return main.call(lambda: (len(self._live), len(self._waiting)))

    # --- main: the live set and the queue -----------------------------------------------------------------------

    def _settle(self, run: _Run, result: GraniteError | None) -> None:
        waiter, run.waiter = run.waiter, None
        if waiter is not None:
            waiter.result = result
            waiter.settled.set()

    def _start_waiting(self) -> None:
        while self._waiting and (self.lanes < 1 or len(self._live) < self.lanes):
            self._launch(self._waiting.pop(0))

    def _start_turn(self, run: _Run) -> None:
        self._counter += 1
        run.raw = f"{self.label}-{self._counter}"
        # Cancelled before it could start: it ends as a queued run cancelled would.
        if run.requested is not None and run.requested.is_set:
            self._state.cancelled += 1
            self._settle(run, Cancelled())
            return
        # A key that is live, or waiting, is the same call asked for again: the newer replaces it.
        if any(live.key == run.key for live in self._live):
            self._cancel_turn(run.key)
        for queued in self._waiting:
            if queued.key == run.key:
                self._waiting.remove(queued)
                self._state.cancelled += 1
                self._settle(queued, Cancelled())
                break
        if self.lanes >= 1 and len(self._live) >= self.lanes:
            self._waiting.append(run)
            return
        self._launch(run)

    def _cancel_turn(self, key: str) -> None:
        for run in self._live:
            if run.key == key:
                self._live.remove(run)
                run.cancel.set()
                self._state.cancelled += 1
                self._settle(run, Cancelled())
                self._start_waiting()
                return
        for run in self._waiting:
            if run.key == key:
                self._waiting.remove(run)
                self._state.cancelled += 1
                self._settle(run, Cancelled())
                return

    def _launch(self, run: _Run) -> None:
        self._live.append(run)
        self._state.runs += 1
        try:
            threading.Thread(target=self._thread, args=(run,), name="granite.run", daemon=True).start()
        except RuntimeError as error:
            ended = _Frame(run, True)
            ended.error = Fault(f"{run.raw} failed: a thread could not start ({error})")
            self._enqueue(ended)

    # --- the run's thread ---------------------------------------------------------------------------------------

    def _enqueue(self, frame: _Frame) -> None:
        def add(inbox: _Inbox) -> bool:
            inbox.frames.append(frame)
            schedule = not inbox.scheduled
            inbox.scheduled = True
            return schedule

        if self._inbox.with_(add):
            main.enqueue(self._drain)

    def _thread(self, run: _Run) -> None:
        ended = _Frame(run, True)
        try:
            locked.check("started a run")
            try:
                compute = self._compute() if self._compute is not None else None
            except BaseException as made:
                # Not the run's own error: the engine says it, as Rust's AgentRunError::Failed.
                if not run.cancel.is_set:
                    ended.error = Fault(f"{run.raw} failed: {fault_of(made).said}")
                    ended.error.__cause__ = made
            else:
                context = Context(run.key, self.word, run.host, run.cancel, compute,
                                  lambda event: self._enqueue(_Frame(run, False, event)))
                try:
                    self._body(context, run.job)
                except BaseException as error:
                    ended.error = fault_of(error)
                    if ended.error is not error:
                        ended.error.__cause__ = error
                del context
            if run.cancel.is_set:
                ended.cancelled, ended.error = True, None
        except BaseException:  # the engine's own fault: said, and the run still ends
            sys.stderr.write("granite: a run's thread raised:\n" + traceback.format_exc())
            ended.error = ended.error or Fault(f"{run.raw} failed: the engine raised")
        finally:
            self._enqueue(ended)

    # --- main: draining -----------------------------------------------------------------------------------------

    def _drain(self) -> None:
        def take(inbox: _Inbox) -> list[_Frame]:
            frames, inbox.frames = inbox.frames, []
            inbox.scheduled = False
            return frames

        frames = self._inbox.with_(take)
        for frame in frames:
            run = frame.run
            # A frame of a run that was cancelled is dropped: the run is over for whoever listens.
            if not any(live is run for live in self._live):
                continue
            if not frame.ended:
                self._state.events += 1
                try:
                    self._taps.send(RunEvent(run.key, frame.event))
                except BaseException:  # a tap that raises is a kernel bug: said, and the drain goes on
                    sys.stderr.write("granite: a run engine's tap raised:\n" + traceback.format_exc())
                continue
            self._live.remove(run)
            if frame.cancelled:
                self._state.cancelled += 1
                self._settle(run, Cancelled())
            elif frame.error is not None:
                self._state.failed += 1
                self._state.last_failure = frame.error.said
                self._settle(run, frame.error)
            else:
                self._state.finished += 1
                self._settle(run, None)
            self._start_waiting()
