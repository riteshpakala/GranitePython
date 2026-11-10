"""``parrot``: a package agent that exercises every message of the wire. It says its text back, notes as it goes, asks
the host's model when told, waits when told, refuses, faults, and crashes when told.

GraniteRust's ``granite_conformance::Parrot``, line for line, so its ``--describe`` and its answers are the Rust
parrot's bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import granite
from granite.schema import float_display


@dataclass
class ParrotInput:
    text: str = granite.field(note="what to say")
    notes: granite.u32 | None = None
    ask_model: bool | None = None
    delay: float | None = None
    crash: bool | None = None
    fail: bool | None = None
    fault: bool | None = None
    directory: str | None = None


@dataclass
class ParrotSaid:
    said: str
    model_said: int | None = None
    directory: str | None = None
    host_directory: str | None = None
    ask: str | None = None
    pid: int = 0


class Parrot(granite.AgentPackage):
    word = "parrot"
    summary = "says its text back, in a process of its own."
    guide = "Give it text; it says it back."
    Input = ParrotInput

    def compute(self):
        return None

    def output(self, events):
        return granite.Answer(events[-1]) if events else None

    def run(self, input: ParrotInput, context: granite.Context) -> None:
        print("this line must not reach the wire")
        for index in range(input.notes or 0):
            context.note(f"note {index + 1}")
        if input.crash:
            os.abort()
        if input.fail:
            raise granite.Refusal("call", "told to fail: leave out `fail`", said="told to fail")
        if input.fault:
            raise RuntimeError("it broke")
        if input.delay is not None:
            context.note(f"waiting {float_display(input.delay)} s as pid {os.getpid()}")
            context.cancel.wait(input.delay)
        said = ParrotSaid(said=input.text, directory=input.directory, host_directory=context.directory,
                          ask=context.ask, pid=os.getpid())
        if input.ask_model:
            schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
            answer = context.ask_object(schema, "Count.", "How many?", 16, 5.0)
            n = answer.get("n") if isinstance(answer, dict) else None
            said.model_said = n if type(n) is int and -(2**63) <= n < 2**63 else None
        context.deliver(said)


roster = granite.Roster(Parrot)
