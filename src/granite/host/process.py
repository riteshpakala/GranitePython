"""The host's side of granite-agent/1: a runner described, and one call made to it in a process of its own, its notes
heard, its asks of the host's model answered, its cancel passed on, and its end said as Rust says it. GraniteRust's
RemoteAgentProcess; GraniteC's process.c.

PIN: ONE PROCESS PER CALL. A call starts its runner, says ``perform`` after the runner's ``hello``, and reads until
the one ``output`` or ``failed``. A runner still there five seconds after its call ended is terminated, and what its
group left behind is killed.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from granite import agentjson, wire
from granite.context import CancelFlag, Host
from granite.core.emitter import Anchor
from granite.error import GraniteError, Refusal
from granite.host.child import Child, Ended, status_text
from granite.host.remote import ENDED, FAILED, FAULT, REFUSED, UNREADABLE, RemoteError

_KEYS = {wire.Hello: "hello", wire.Perform: "perform", wire.Note: "note", wire.ModelAsk: "model",
         wire.ModelAnswer: "answer", wire.Output: "output", wire.Failed: "failed", wire.Cancel: "cancel",
         wire.Entries: "entries"}


@dataclass
class Cost:
    """What a call cost: its seconds, its CPU seconds, its peak resident bytes, how it ended, and its model asks."""

    seconds: float = 0.0
    cpu_seconds: float = 0.0
    peak_bytes: int = 0
    status: str = ""
    asks: int = 0

    def sentence(self) -> str:
        megabytes = self.peak_bytes / 1048576.0
        peak = f"{megabytes / 1024.0:.1f} GB" if megabytes >= 1024.0 else f"{megabytes:.0f} MB"
        return f"released · peak {peak} · {self.cpu_seconds:.1f} s CPU in {self.seconds:.1f} s"

    def json(self) -> dict:
        return {"seconds": float(self.seconds), "cpuSeconds": float(self.cpu_seconds), "peakBytes": self.peak_bytes,
                "status": self.status, "asks": self.asks}


class Process:
    """A runner, started once per call with its environment and ``GRANITE_AGENT=1``."""

    def __init__(self, executable: str, environment: Mapping[str, str] | None = None) -> None:
        if not executable.startswith("/") and "/" in executable:
            try:
                executable = f"{os.getcwd()}/{executable}"
            except OSError:
                pass
        self.executable = executable
        source = os.environ if environment is None else environment
        self.environment = {k: v for k, v in source.items() if k != "GRANITE_AGENT"}
        self.environment["GRANITE_AGENT"] = "1"

    def describe(self) -> list:
        """The runner's entries (``--describe``); raises ``RemoteError``."""
        child = Child(self.executable, ["--describe"], None, self.environment)
        child.channel.close()
        entries = None
        while entries is None and (message := child.channel.recv()) is not None:
            if isinstance(message, wire.Entries):
                entries = message.entries
        end = child.wait()
        if entries is None:
            raise _ended(os.path.basename(self.executable), child, end)
        return entries

    def perform(self, word: str, input: bytes | str, host: Host | None = None,
                cancel: CancelFlag | None = None) -> tuple[bytes, Cost]:
        """One call: the answer's bytes, and what it cost. Raises ``RemoteError`` (its ``cost`` set once the runner
        started)."""
        try:
            value = agentjson.read(input)
        except agentjson.JSONError:
            raise RemoteError(UNREADABLE, word, reason="the input is not JSON") from None
        offered = host or Host.none()
        try:
            child = Child(self.executable, [], offered.directory, self.environment)
        except RemoteError as error:
            error.word = word
            raise
        call = wire.Perform(word, value, offered.directory, offered.ask, offered.model is not None)
        watching = None
        if cancel is not None:
            watching = Anchor()

            def cancel_runner(receiver: Any, flag: None) -> None:
                child.channel.send(wire.Cancel())
                child.terminate(2.0)

            cancel.on(watching, cancel_runner)
        asks = [0]
        try:
            result, said = _serve(child, word, call, offered, asks)
        finally:
            del watching
        child.channel.close()

        def end_straggler() -> None:
            if child.wait(5.0) is None:
                child.terminate(2.0)

        threading.Thread(target=end_straggler, name="granite.straggler", daemon=True).start()
        end = child.wait()
        cost = Cost(child.seconds, end.cpu_seconds, end.peak_bytes, status_text(end.status), asks[0])
        if result is not None and result.kind == ENDED:
            result = _ended(word, child, end)
        if result is not None:
            result.cost = cost
            raise result
        return agentjson.write(said), cost


def _ended(word: str, child: Child, end: Ended) -> RemoteError:
    return RemoteError(ENDED, word, status=status_text(end.status), log=child.log())


def _answer(channel: Any, host: Host, ask: wire.ModelAsk) -> None:
    answer = wire.ModelAnswer(ask.id)
    model = host.model
    if model is None:
        answer.error = "this host has no model loaded"
    else:
        try:
            if ask.kind == "object":
                answer.object = model.object(ask.schema if ask.schema is not None else {}, ask.system, ask.user,
                                             ask.max_tokens, ask.seconds, None)
            else:
                answer.text = model.text(ask.system, ask.user, ask.max_tokens, ask.seconds, None)
        except GraniteError as error:
            answer.object, answer.text, answer.error = None, None, error.said
        except Exception as error:  # the host's model failed by its own fault: the runner hears why
            answer.object, answer.text, answer.error = None, None, str(error) or type(error).__name__
    channel.send(answer)


_MAJOR = re.compile(r"\+?[0-9]+\Z")


def _serve(child: Child, word: str, call: wire.Perform, host: Host,
           asks: list[int]) -> tuple[RemoteError | None, Any]:
    """Reads the runner until its one output or failed: (None, the output) or (why there is none, None)."""
    channel = child.channel
    sent = standard = False
    while True:
        message = channel.recv()
        if message is None:
            return RemoteError(ENDED, word), None
        if isinstance(message, wire.Hello):
            major = message.wire[len("granite-agent/"):] if message.wire.startswith("granite-agent/") else ""
            if not _MAJOR.match(major) or int(major) != 1:
                return RemoteError(UNREADABLE, word,
                                   reason=f"it speaks {message.wire}, and this host {wire.WIRE}"), None
            if sent:
                continue
            sent = True
            standard = message.answer is not None
            why = channel.send(call)
            if why is not None:
                return RemoteError(UNREADABLE, word, reason=f"the call could not be sent: {why}"), None
        elif isinstance(message, wire.Note):
            host.note(message.text)
        elif isinstance(message, wire.ModelAsk):
            asks[0] += 1
            threading.Thread(target=_answer, args=(channel, host, message), name="granite.answer",
                             daemon=True).start()
        elif isinstance(message, wire.Output):
            return None, message.value
        elif isinstance(message, wire.Failed):
            failure = message.error
            if isinstance(failure, Refusal):
                return RemoteError(REFUSED, word, message=failure.said, refusal=failure), None
            return RemoteError(FAULT if standard else FAILED, word, message=failure.said), None
        else:
            return RemoteError(UNREADABLE, word, reason=f"it said `{_KEYS[type(message)]}` during a call"), None
