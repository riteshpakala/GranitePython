"""Why a call was not answered: a refusal with a kind a host can count and reasons that each say their fix, the
agent's own fault said as it is, or a cancel. GraniteRust's AgentRefusal, SaidRefusal and AgentPackageError.

PIN: A REFUSAL IS NOT A FAULT. Only a ``Refusal`` is the call's to correct. Anything else a run raises is the agent's
own fault, said as ``str(error)`` says it.
"""

from __future__ import annotations

from typing import Iterable

#: Whose a refusal is, as a host tells its model.
KINDS = ("input", "call", "build", "device", "model", "parse", "reach", "answer", "host")


def kind_named(name: str) -> str:
    """A refusal kind as written; one this reader does not know reads as ``host``."""
    return name if name in KINDS else "host"


def refusal_text(reasons: Iterable[str]) -> str:
    """How a refusal reads: one reason as it is, several as a count and a list."""
    reasons = list(reasons)
    if len(reasons) == 1:
        return reasons[0]
    return f"{len(reasons)} problems, so nothing was done:" + "".join(f"\n- {reason}" for reason in reasons)


class GraniteError(Exception):
    """A call that was not answered, and the sentence it is said in (``said``)."""

    def __init__(self, said: str) -> None:
        super().__init__(said)
        self.said = said

    def __str__(self) -> str:
        return self.said


class Refusal(GraniteError):
    """A refusal: its kind, the reasons a host counts, and the sentence a person reads.

    ``Refusal("call", "say what to draw")`` is one reason said as itself; ``Refusal("answer", reasons=[…])`` several,
    said as a count and a list; ``Refusal("build", reasons=[…], said="the comic did not build")`` a refusal in the
    package's own sentence (Rust's AgentRefusing::refused).
    """

    def __init__(self, kind: str, reason: str | None = None, *, reasons: Iterable[str] | None = None,
                 said: str | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"granite: a refusal's kind is one of {', '.join(KINDS)}, not {kind!r}")
        listed = list(reasons) if reasons is not None else []
        if reason is not None:
            listed.insert(0, reason)
        if not listed:
            raise ValueError("granite: a refusal says at least one reason")
        super().__init__(said if said is not None else refusal_text(listed))
        self.kind = kind
        self.reasons = listed


class Fault(GraniteError):
    """The agent's own fault, said as it is."""


class Cancelled(GraniteError):
    """The call was cancelled."""

    def __init__(self, said: str = "cancelled") -> None:
        super().__init__(said)


def fault_of(error: BaseException) -> GraniteError:
    """What a run's exception is to a host: a refusal or cancel as it is, anything else a fault said as it is."""
    if isinstance(error, GraniteError):
        return error
    return Fault(str(error) or type(error).__name__)


# What asking the host's model can say besides an answer: each a model refusal, in Rust's sentence.

def host_no_model(word: str) -> Refusal:
    return Refusal("model", f"{word} asks the host's model, and this host has none loaded")


def host_no_answer(reason: str) -> Refusal:
    return Refusal("model", f"the host's model gave no answer: {reason}")


def host_bad_answer(reason: str) -> Refusal:
    return Refusal("model", f"the host's model answered something that does not decode: {reason}")
