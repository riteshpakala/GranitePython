"""What calling an agent in another process can say: GraniteRust's RemoteAgentError, sentence for sentence;
GraniteC's remote.c."""

from __future__ import annotations

from granite.error import Refusal

FAILED, REFUSED, FAULT, ENDED, UNREADABLE, CHANGED, SPAWN = (
    "failed", "refused", "fault", "ended", "unreadable", "changed", "spawn")


class RemoteError(Exception):
    """Why a call to a runner was not answered, and the sentence a person reads (``str``)."""

    def __init__(self, kind: str, word: str, *, message: str | None = None, refusal: Refusal | None = None,
                 status: str | None = None, log: str | None = None, reason: str | None = None,
                 path: str | None = None) -> None:
        self.kind, self.word, self.message, self.refusal = kind, word or "", message, refusal
        self.status, self.log, self.reason, self.path = status, log, reason, path
        self.cost = None  # what the call cost, once its runner started
        super().__init__(self.text)

    @property
    def text(self) -> str:
        w = self.word
        if self.kind == FAILED:
            return f"{w} failed: {self.message}"
        if self.kind == REFUSED:
            return f"{w} refused ({self.refusal.kind if self.refusal else 'host'}): {self.message}"
        if self.kind == FAULT:
            return f"{w} failed by its own fault: {self.message}"
        if self.kind == ENDED:
            if self.log:
                return f"{w}'s process ended ({self.status}) without answering: {self.log}"
            return f"{w}'s process ended ({self.status}) without answering"
        if self.kind == UNREADABLE:
            return f"{w}'s process broke the wire: {self.reason}"
        if self.kind == CHANGED:
            return f"{w}'s executable changed since it was installed ({self.path}); install it again"
        if self.kind == SPAWN:
            return f"could not start {self.path}: {self.reason}"
        return ""

    def __str__(self) -> str:
        return self.text
