"""An agent that ships as a package of its own: a word, what it does, an input it declares, a run that streams events,
and an answer read off them. Swift Granite's GraniteAgentPackage, GraniteRust's AgentPackage, GraniteC's package.c.

    @dataclass
    class Input:
        text: str = granite.field(note="what to say")

    class Echo(granite.AgentPackage):
        word = "echo"
        summary = "Says it back."
        Input = Input

        def run(self, input, context):
            context.deliver(granite.Answer(said=input.text))

PIN: COMPUTE IS MADE ON THE FIRST CALL, never when the process starts: a package that holds a device or a model costs
nothing until it is asked for, and ``--describe`` makes none. It is made once per process; a compute that fails is
tried again on the next call.

PIN: A RUN BLOCKS, ON ITS OWN THREAD. It streams with ``context.deliver``, checks ``context.is_cancelled``, and returns,
or raises a ``Refusal`` (or anything else: its fault). An ``async def run`` runs in an event loop of its own there,
and a cancel cancels its task.

PIN: THE PIPELINE IS RUST'S, STEP FOR STEP: read the input, check it, tap the run's events, perform, map what it ended
with, read the answer off its events, check it against granite-answer/1, stamp it, and write it sorted.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import os
import re
import threading
import time
from typing import Any, ClassVar, Iterable, Sequence

from granite import agentjson, schema
from granite.answer import FORMAT, Make, problems, stamped
from granite.context import CancelFlag, Context, Host
from granite.core.emitter import Anchor
from granite.error import Refusal, refusal_text
from granite.runs import RunEvent, Runs

#: ``lanes`` for a package with no bound on how many of its calls run at once.
UNBOUNDED = -1

_WORD = re.compile(r"[a-z][a-z0-9-]*\Z")


class AgentPackage:
    """A package an author writes: class attributes say what it is, ``run`` does it."""

    #: The word a host calls it by: lowercase, one word, unique among what a host runs.
    word: ClassVar[str] = ""
    #: What it does, in a sentence a host shows a person or a model.
    summary: ClassVar[str] = ""
    #: How to use it well, in its own words.
    guide: ClassVar[str | None] = None
    #: The module a host names it by beside its word (default: the class's top-level module).
    module: ClassVar[str | None] = None
    #: Its type, as ``Module.Type`` (default: module and class name).
    type: ClassVar[str | None] = None
    #: How many calls run at once: 0 for two, ``UNBOUNDED`` for no bound.
    lanes: ClassVar[int] = 0
    #: What it makes, one ``Make`` per kind.
    makes: ClassVar[Sequence[Make]] = ()
    #: What its input takes: a dataclass, or None for any JSON.
    Input: ClassVar[Any] = None

    def compute(self) -> Any:
        """Makes what every run of this process shares (a device, a model), on the first call."""
        return None

    def run(self, input: Any, context: Context) -> Any:
        """The run, on its own thread: streams events with ``context.deliver``; raises a ``Refusal`` to refuse."""
        raise NotImplementedError(f"{type(self).__name__} does not say how it runs: give it a run(input, context)")

    def output(self, events: list) -> Any:
        """The answer, read off the run's events (JSON values, in order): the last, by default. None: no answer."""
        return events[-1] if events else None


# --- describing -----------------------------------------------------------------------------------------------------

def module_of(package: AgentPackage) -> str:
    kind = type(package)
    return package.module if package.module is not None else kind.__module__.split(".")[0]


def type_of(package: AgentPackage) -> str:
    return package.type if package.type is not None else f"{module_of(package)}.{type(package).__name__}"


def lanes_of(package: AgentPackage) -> int:
    """How many calls run at once, or -1 for no bound."""
    if package.lanes == 0:
        return 2
    return -1 if package.lanes < 0 else package.lanes


def entry(package: AgentPackage) -> dict:
    """What ``--describe`` says of the package: its entry, as Rust's Entry::of writes it."""
    input_schema = schema.of(package.Input)
    said: dict[str, Any] = {
        "kind": "package",
        "word": package.word,
        "summary": package.summary,
        "module": module_of(package),
        "type": type_of(package),
        "schema": input_schema.value(),
        "fields": input_schema.notes(),
    }
    lanes = lanes_of(package)
    if lanes > 0:
        said["lanes"] = lanes
    if package.guide:
        said["guide"] = package.guide
    if package.makes:
        said["makes"] = [make.entry() for make in package.makes]
    return said


def _checked(package: Any) -> AgentPackage:
    if isinstance(package, type) and issubclass(package, AgentPackage):
        package = package()
    if not isinstance(package, AgentPackage):
        raise TypeError(f"granite: a roster holds AgentPackage classes or instances, not {package!r}")
    name = type(package).__name__
    if not isinstance(package.word, str) or not _WORD.match(package.word):
        raise ValueError(f"granite: {name}'s word is {package.word!r}: one lowercase word, [a-z][a-z0-9-]*")
    if not isinstance(package.summary, str) or not package.summary:
        raise ValueError(f"granite: {name} says no summary: one sentence a host shows a person or a model")
    schema.of(package.Input)  # a type an input cannot take is said now, not on the first call
    return package


class Roster:
    """The packages a runner carries, in order; the first package of a word wins."""

    def __init__(self, *packages: Any) -> None:
        self.packages: tuple[AgentPackage, ...] = tuple(_checked(p) for p in packages)

    def __iter__(self):
        return iter(self.packages)

    def __len__(self) -> int:
        return len(self.packages)

    def find(self, word: str) -> AgentPackage | None:
        """The roster's package called ``word``."""
        for package in self.packages:
            if package.word == word:
                return package
        return None

    def entries(self) -> list[dict]:
        """Every entry, as ``--describe`` lists them."""
        return [entry(p) for p in self.packages if self.find(p.word) is p]


# --- a package's host: one engine, and its compute made once --------------------------------------------------------

class _PackageHost:
    def __init__(self, package: AgentPackage) -> None:
        self.package = package
        self.made = threading.Condition()
        self.state = "idle"  # idle, making, made
        self.compute: Any = None
        overridden = type(package).compute is not AgentPackage.compute
        self.runs = Runs(package.word, self._body, self._compute if overridden else None, lanes_of(package))

    def _compute(self) -> Any:
        """Rust's OnceCell::get_or_try_init: one caller makes it while the rest wait; a failure leaves it for the next
        caller to try."""
        with self.made:
            while self.state == "making":
                self.made.wait()
            if self.state == "made":
                return self.compute
            self.state = "making"
        try:
            made = self.package.compute()
        except BaseException:
            with self.made:
                self.state = "idle"
                self.made.notify_all()
            raise
        with self.made:
            self.compute, self.state = made, "made"
            self.made.notify_all()
        return made

    def _body(self, context: Context, input: Any) -> None:
        run = self.package.run
        if inspect.iscoroutinefunction(run):
            asyncio.run(_cancellable(run(input, context), context.cancel))
        else:
            result = run(input, context)
            if inspect.iscoroutine(result):
                asyncio.run(_cancellable(result, context.cancel))


async def _cancellable(coroutine: Any, cancel: CancelFlag) -> Any:
    """Runs ``coroutine`` as a task a cancel cancels."""
    task = asyncio.ensure_future(coroutine)
    loop = asyncio.get_running_loop()
    watching = Anchor()

    def wake(receiver: Any, value: None) -> None:
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # the loop is closed: the run is over
            pass

    cancel.on(watching, wake)
    try:
        return await task
    finally:
        del watching


_hosts: dict[int, _PackageHost] = {}
_hosts_lock = threading.Lock()


def _host_of(package: AgentPackage) -> _PackageHost:
    """The package's host, made on its first call and kept for the process."""
    with _hosts_lock:
        found = _hosts.get(id(package))
        if found is not None and found.package is package:
            return found
    made = _PackageHost(package)
    with _hosts_lock:
        return _hosts.setdefault(id(package), made)


# --- a call ---------------------------------------------------------------------------------------------------------

_count = itertools.count(1)


def call_id(word: str) -> str:
    """``{word}-{8 hex}``, mixed as Rust mixes it: the clock's nanoseconds, the process, and a count."""
    nanos = time.time_ns() % 1_000_000_000
    mixed = (nanos ^ (os.getpid() << 16)) + next(_count) * 0x9E3779B9
    return f"{word}-{mixed & 0xFFFFFFFF:08x}"


def _input_refusal(word: str, reason: str) -> Refusal:
    return Refusal("input", reasons=[reason], said=f"{word} could not read its input: {reason}")


class _Collected:
    """The tap that keeps every event of one call, on main."""

    __slots__ = ("key", "events", "__weakref__")

    def __init__(self, key: str) -> None:
        self.key = key
        self.events: list = []


def _collect(collected: _Collected, heard: RunEvent) -> None:
    if heard.key == collected.key:
        collected.events.append(heard.event)


def perform(package: AgentPackage, input: bytes | str, host: Host | None = None,
            cancel: CancelFlag | None = None) -> bytes:
    """One call, in this process: ``input`` (JSON) read and checked against the package's schema, the run hosted on
    the package's own engine and offered ``host``, the answer checked, stamped and written: its canonical bytes.
    Raises why the call was not answered: an input or answer refusal, the run's own error, or ``Cancelled``. Setting
    ``cancel`` cancels the call. Blocks; never call it from granite.main."""
    word = package.word
    try:
        value = agentjson.read(input)
    except agentjson.JSONError as error:
        raise _input_refusal(word, str(error)) from None
    try:
        job = schema.of(package.Input).decode(value)
    except ValueError as error:
        raise _input_refusal(word, str(error)) from None

    ours = _host_of(package)
    key = call_id(word)
    # The tap before the run, so no event is missed; it ends with the call.
    collected = _Collected(key)
    ours.runs.tap(collected, _collect)
    watching = None
    if cancel is not None:
        watching = Anchor()
        runs = ours.runs
        cancel.on(watching, lambda receiver, value: runs.cancel(key))
    try:
        ours.runs.perform(key, host, cancel, job)
    finally:
        del watching
    # Settled: every event of this call reached the tap, and none comes after.
    events, collected.events = collected.events, []
    del collected

    if type(package).output is AgentPackage.output:
        if not events:
            raise Refusal("answer", f"{word} finished without an answer")
        output = events[-1]
    else:
        output = package.output(events)
        if output is None:
            raise Refusal("answer", f"{word} finished without an answer")
        output = schema.encode(output)
    if type(output) is not dict:
        raise Refusal("answer", f"{word} answered something that is not an object")
    said = problems(output, word, package.makes, host.directory if host else None)
    if said:
        raise Refusal("answer", reasons=said, said=f"{word}'s answer breaks {FORMAT}: {refusal_text(said)}")
    return agentjson.write(stamped(output, word))


def perform_value(package: AgentPackage, input: Any, host: Host | None = None,
                  cancel: CancelFlag | None = None) -> dict:
    """``perform`` with a JSON value in and the stamped answer out, for tests and scripts."""
    return agentjson.read(perform(package, agentjson.write(schema.encode(input)), host, cancel))


__all__: Iterable[str] = ("AgentPackage", "Roster", "UNBOUNDED", "entry", "perform", "perform_value", "call_id")
