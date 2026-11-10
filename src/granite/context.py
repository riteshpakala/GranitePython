"""What a run reaches the world through: whether it was asked to stop, the host that offered it a folder, an ask and
a model, and the way its events reach the one writer. GraniteRust's CancelFlag, AgentHost, AgentHostModel and
AgentContext.

PIN: CANCELLING IS COOPERATIVE. A run is never killed: its flag is set, an ask of the host's model it is blocked in
wakes, and the run is expected to return. Events it delivers after are dropped.

PIN: OFFERED, NEVER ASSUMED. A host may offer no folder, no ask and no model; a run that cannot work without the model
asks, and the ask refuses (kind ``model``) naming the package.

PIN: THE ASK IS NOT A PARAMETER. A model writes a call's input; it never writes what the person asked.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from granite import schema
from granite.core import locked
from granite.core.emitter import Emitter
from granite.error import host_bad_answer, host_no_answer, host_no_model


class CancelFlag:
    """Whether a run was asked to stop. Set once; later sets do nothing. Safe from any thread."""

    __slots__ = ("_set", "_listeners", "__weakref__")

    def __init__(self) -> None:
        self._set = threading.Event()
        self._listeners: Emitter[None] = Emitter("a cancel flag's listeners")

    def set(self) -> None:
        """Sets the flag and tells whoever listens, on this thread."""
        if self._set.is_set():
            return
        self._set.set()
        self._listeners.send(None)

    @property
    def is_set(self) -> bool:
        return self._set.is_set()

    def wait(self, seconds: float | None = None) -> bool:
        """Blocks until the flag is set, or ``seconds`` pass; whether it was set."""
        return self._set.wait(seconds)

    def on(self, receiver: Any, handler: Callable[[Any, None], None]) -> None:
        """Calls ``handler(receiver, None)`` when the flag is set, while ``receiver`` lives; at once when it is set
        already. A wake may come twice."""
        self._listeners.on(receiver, handler)
        if self._set.is_set():
            handler(receiver, None)


class HostModel:
    """The model a host has loaded, as a package may ask it. Both asks block until it answers, gives up, or ``cancel``
    is set; a host error is raised as a model refusal (``host_no_answer``, ``host_bad_answer``)."""

    def object(self, schema: Any, system: str, user: str, max_tokens: int, seconds: float,
               cancel: CancelFlag | None = None) -> Any:
        """A JSON object that fits ``schema`` (a JSON Schema)."""
        raise NotImplementedError

    def text(self, system: str, user: str, max_tokens: int, seconds: float, cancel: CancelFlag | None = None) -> str:
        """Text, whole."""
        raise NotImplementedError


class Host:
    """What a host offers a run beside its input: its folder and the person's ask (either may be None), its model, and
    where a line of progress goes."""

    __slots__ = ("directory", "ask", "model", "_note")

    def __init__(self, directory: str | None = None, ask: str | None = None, model: HostModel | None = None,
                 note: Callable[[str], None] | None = None) -> None:
        self.directory = directory
        self.ask = ask
        self.model = model
        self._note = note

    @classmethod
    def none(cls) -> "Host":
        """A host that offers nothing: a call made with JSON alone."""
        return cls()

    def note(self, line: str) -> None:
        """A line of progress, shown as the host shows its agents' work."""
        if self._note is not None:
            self._note(line)


class Context:
    """A run's context: its id, its package's word and compute, its host, its cancel flag, and its events' way out."""

    __slots__ = ("run", "word", "host", "cancel", "compute", "_sink")

    def __init__(self, run: str, word: str, host: Host | None = None, cancel: CancelFlag | None = None,
                 compute: Any = None, sink: Callable[[Any], None] | None = None) -> None:
        self.run = run
        self.word = word
        self.host = host or Host.none()
        self.cancel = cancel or CancelFlag()
        self.compute = compute
        self._sink = sink

    def deliver(self, event: Any) -> None:
        """Delivers ``event`` into this run's stream: a copy, as plain JSON (a dataclass by its wire names). Safe from
        any thread. Raises ``TypeError`` for something that is not JSON."""
        copied = schema.encode(event)
        if self._sink is not None:
            self._sink(copied)

    def note(self, line: str) -> None:
        """A line of progress, through the host."""
        self.host.note(str(line))

    @property
    def is_cancelled(self) -> bool:
        return self.cancel.is_set

    @property
    def directory(self) -> str | None:
        return self.host.directory

    @property
    def ask(self) -> str | None:
        return self.host.ask

    def ask_object(self, schema: Any, system: str, user: str, max_tokens: int = 3000, seconds: float = 300) -> Any:
        """Asks the host's model for a JSON object that fits ``schema``; blocks until it answers. A host without a
        model, no answer, and a cancelled call are model refusals."""
        model = self._model()
        locked.check("asked the host's model")
        return model.object(schema, system or "", user or "", max_tokens, seconds, self.cancel)

    def ask_as(self, kind: type, system: str, user: str, max_tokens: int = 3000, seconds: float = 300) -> Any:
        """Asks the host's model for a ``kind`` (a dataclass): its JSON Schema read off the class, the answer decoded
        as an input is. An answer that does not decode is a model refusal saying why (Rust's ``object_as``)."""
        shape = schema.of(kind)
        answer = self.ask_object(shape.json_schema(), system, user, max_tokens, seconds)
        try:
            return shape.decode(answer)
        except ValueError as error:
            raise host_bad_answer(str(error)) from None

    def ask_text(self, system: str, user: str, max_tokens: int = 3000, seconds: float = 300) -> str:
        """Asks the host's model for text."""
        model = self._model()
        locked.check("asked the host's model")
        return model.text(system or "", user or "", max_tokens, seconds, self.cancel)

    def _model(self) -> HostModel:
        if self.host.model is None:
            raise host_no_model(self.word)
        if self.cancel.is_set:
            raise host_no_answer("the call was cancelled")
        return self.host.model
