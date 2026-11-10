"""State behind a lock: Swift's GraniteLock<State>, GraniteRust's Locked<T>, GraniteC's granite_lock.

PIN: NOTHING IS DROPPED UNDER A LOCK. Dropping the last reference to an object runs whatever it owned alone (a
handler's captures, a run's compute, an object's ``__del__``) on this thread, now. ``Locked.with_`` hands back what
its body returns after the lock is released, so a body that returns what it removed lets it go after the unlock.

PIN: ONE LOCK AT A TIME, AND NOTHING CALLED UNDER IT. A thread that takes a second ``Locked`` while holding one, or
reaches the author's code while holding one, raises ``LockRuleError`` naming both. Swift's compiler and Rust's borrow
checker hold most of this; here it is held at run time, as in GraniteC.

PIN: A FINALIZER MAY RUN UNDER A LOCK. Python's cycle collector can run any object's ``__del__`` on whichever thread
allocates, even one inside a locked body. What such a finalizer asks of the kernel (an emitter's ``on``, ``off``,
``send``) is deferred until this thread lets its lock go: ``defer``. In ``strict`` mode (tests) it is recorded in
``violations`` instead, so a test sees the kernel broke its own rule.
"""

from __future__ import annotations

import threading
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_held = threading.local()

#: Tests set this: a kernel operation asked for under a lock is recorded and refused instead of deferred.
strict = False
#: What strict mode caught: one sentence each.
violations: list[str] = []


class LockRuleError(RuntimeError):
    """A thread broke the lock rule: it took a second lock, or reached the author's code, while holding one."""


def _holding() -> "Locked | None":
    return getattr(_held, "lock", None)


def held() -> bool:
    """Whether this thread holds a ``Locked``."""
    return _holding() is not None


def check(what: str) -> None:
    """Raises ``LockRuleError`` saying ``what`` was done under a lock, when this thread holds one. Called before every
    call into the author's code."""
    holding = _holding()
    if holding is not None:
        raise LockRuleError(f"granite: {what} while holding {holding.name}")


def defer(operation: Callable[[], None], what: str) -> bool:
    """When this thread holds a lock, keeps ``operation`` to run once it is released and says True; else False, and
    the caller runs it now. In strict mode a held lock is a violation, recorded and raised."""
    holding = _holding()
    if holding is None:
        return False
    if strict:
        sentence = f"granite: {what} while holding {holding.name}"
        violations.append(sentence)
        raise LockRuleError(sentence)
    pending = getattr(_held, "deferred", None)
    if pending is None:
        pending = _held.deferred = []
    pending.append(operation)
    return True


class Locked(Generic[T]):
    """A value and the one lock that guards it. The body of ``with_`` reads and changes the value, calls nothing, and
    returns what it removed."""

    __slots__ = ("_lock", "_value", "name")

    def __init__(self, value: T, name: str = "a lock") -> None:
        self._lock = threading.Lock()
        self._value = value
        self.name = name

    def with_(self, body: Callable[[T], R]) -> R:
        """Runs ``body`` with the lock held; what it returns is handed back once the lock is released."""
        holding = _holding()
        if holding is not None:
            raise LockRuleError(f"granite: took {self.name} while holding {holding.name}")
        with self._lock:
            _held.lock = self
            try:
                result = body(self._value)
            finally:
                _held.lock = None
        _run_deferred()
        return result

    def update(self, body: Callable[[T], "tuple[T, R]"]) -> R:
        """Runs ``body`` with the lock held; it returns the new value and a result, and the result (what it removed)
        is handed back once the lock is released."""

        def change(value: T) -> R:
            self._value, result = body(value)
            return result

        return self.with_(change)

    def replace(self, value: T) -> T:
        """Sets the value and hands back the one it replaced, to be let go after the unlock."""

        def swap(_: T) -> T:
            before = self._value
            self._value = value
            return before

        return self.with_(swap)

    def get(self) -> T:
        """The value now, read under the lock."""
        return self.with_(lambda value: value)


def _run_deferred() -> None:
    while True:
        pending = getattr(_held, "deferred", None)
        if not pending:
            return
        _held.deferred = []
        for operation in pending:
            operation()
