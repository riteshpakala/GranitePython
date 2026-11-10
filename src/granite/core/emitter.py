"""Values sent to receivers held weakly: Swift's GraniteEmitter, GraniteRust's Emitter<T>, GraniteC's granite_emitter.

PIN: NO TOKENS. ``on`` keeps its receiver weakly and hands it to the handler on every send, so the handler need not
hold it. The subscription ends when the receiver is released: nothing is stored, cancelled, or torn down in a
``__del__``. ``off`` removes one early and is never needed for correctness.

PIN: DELIVERY IS SYNCHRONOUS, IN ORDER, LOCK RELEASED. The subscriptions are an immutable tuple, swapped whole under
the lock; a send reads it without the lock and delivers on the sender's thread, so a handler may send, subscribe or
unsubscribe on the same emitter. A receiver released earlier in the same send is not reached; an ``off`` or a
replaced key takes effect from the next send.

PIN: NOTHING IS DROPPED UNDER THE LOCK, AND NO RECEIVER IS LOADED UNDER IT. What a change removes (a subscription and
the handler it owns) is handed back and dropped after the unlock; which receivers are gone is read before the lock is
taken, since loading a weak reference makes this thread an owner that may be the last.

PIN: A HANDLER THAT HOLDS ITS OWN RECEIVER KEEPS IT ALIVE. A handler bound to its receiver, or closing over it, is
refused with a sentence: reach the receiver through the argument the handler is handed.
"""

from __future__ import annotations

import weakref
from typing import Any, Callable, Generic, Hashable, TypeVar

from granite.core import locked
from granite.core.locked import Locked

T = TypeVar("T")

Handler = Callable[[Any, T], None]


class EmitterError(TypeError):
    """A subscription that cannot work as asked: a receiver that cannot be held weakly, or a handler that holds it."""


class Anchor:
    """A bare receiver: an object whose life is the subscription's (GraniteRust's ``Arc<()>``, GraniteC's anchor)."""

    __slots__ = ("__weakref__",)


class Displaceable:
    """A receiver an emitter tells when it drops its subscription to make room: another subscription took its key, or
    the emitter was cleared for a new owner (Swift's GraniteEmitterDisplaceable)."""

    def displaced(self) -> None:
        """Told after the emitter's lock is released. Default: nothing."""


class _Entry:
    __slots__ = ("ref", "key", "handler")

    def __init__(self, ref: "weakref.ref[Any]", key: Hashable | None, handler: Handler) -> None:
        self.ref = ref
        self.key = key
        self.handler = handler


def _holds(handler: Handler, receiver: Any) -> bool:
    """Whether ``handler`` keeps ``receiver`` alive: bound to it, or closing over it."""
    if getattr(handler, "__self__", None) is receiver:
        return True
    for cell in getattr(handler, "__closure__", None) or ():
        try:
            if cell.cell_contents is receiver:
                return True
        except ValueError:  # an empty cell
            continue
    return False


class Emitter(Generic[T]):
    """Sends values to receivers, synchronously and in subscription order. Safe from any thread."""

    __slots__ = ("_entries", "__weakref__")

    def __init__(self, name: str = "an emitter's subscriptions") -> None:
        self._entries: Locked[tuple[_Entry, ...]] = Locked((), name)

    def on(self, receiver: Any, handler: Handler, key: Hashable | None = None) -> None:
        """Subscribes ``receiver`` (held weakly) until it is released: ``handler(receiver, value)`` on every send. With
        a ``key``, the subscription already under it is removed first and this one added last, whoever its receiver
        was; the receiver it displaced is told."""
        try:
            ref = weakref.ref(receiver)
        except TypeError:
            raise EmitterError(
                f"granite: a {type(receiver).__name__} cannot be held weakly: subscribe a granite.Anchor, or an object "
                "whose class has a __weakref__ slot") from None
        if _holds(handler, receiver):
            raise EmitterError("granite: the handler holds its own receiver, so the receiver never goes: pass a "
                               "function, which is handed the receiver on every send")
        entry = _Entry(ref, key, handler)
        if locked.defer(lambda: self._on(entry, receiver), "subscribed on an emitter"):
            return
        self._on(entry, receiver)

    def _on(self, entry: _Entry, receiver: Any) -> None:
        # The snapshot keeps every entry it names alive until the update is done, so an id names one entry.
        snapshot = self._entries._value
        dead = self._gone(snapshot)
        key = entry.key

        def add(entries: tuple[_Entry, ...]) -> tuple[tuple[_Entry, ...], list[_Entry]]:
            kept, removed = [], []
            for existing in entries:
                (removed if id(existing) in dead or (key is not None and existing.key == key) else kept).append(existing)
            kept.append(entry)
            return tuple(kept), removed

        removed = self._entries.update(add)
        del snapshot
        # After the unlock: tell each live receiver displaced by the key (not this one), then let the entries go.
        for gone in removed:
            displaced = gone.ref()
            if displaced is not None and displaced is not receiver and isinstance(displaced, Displaceable):
                locked.check("told a receiver it was displaced")
                displaced.displaced()
            del displaced
        del removed

    def off(self, receiver: Any, key: Hashable | None = None) -> None:
        """Removes ``receiver``'s subscriptions: all of them, or only the one under ``key``."""
        if locked.defer(lambda: self._off(receiver, key), "unsubscribed from an emitter"):
            return
        self._off(receiver, key)

    def _off(self, receiver: Any, key: Hashable | None) -> None:
        # Which entries are this receiver's is read before the lock, as every load of a weak reference is.
        snapshot = self._entries._value
        picked = {id(e) for e in snapshot if e.ref() is receiver and (key is None or e.key == key)}
        dead = self._gone(snapshot)

        def drop(entries: tuple[_Entry, ...]) -> tuple[tuple[_Entry, ...], list[_Entry]]:
            kept = tuple(e for e in entries if id(e) not in picked and id(e) not in dead)
            return kept, [e for e in entries if id(e) in picked or id(e) in dead]

        removed = self._entries.update(drop)
        del snapshot, removed

    def send(self, value: T = None) -> None:  # type: ignore[assignment]
        """Sends ``value`` to every live receiver, in subscription order, on this thread."""
        if locked.defer(lambda: self._send(value), "sent on an emitter"):
            return
        self._send(value)

    def _send(self, value: T) -> None:
        snapshot = self._entries._value  # a tuple: read whole, never changed in place
        released = False
        for entry in snapshot:
            # Loaded now, not when the snapshot was taken: a receiver released earlier in this send is not reached.
            receiver = entry.ref()
            if receiver is None:
                released = True
                continue
            entry.handler(receiver, value)
            del receiver
        del snapshot
        if released:
            self.compact()

    @property
    def receiver_count(self) -> int:
        """How many subscriptions still have a live receiver."""
        return sum(1 for entry in self._entries._value if entry.ref() is not None)

    def remove_all(self) -> None:
        """Removes every subscription, telling the receivers that can be told: an owner taking over."""
        if locked.defer(self._remove_all, "cleared an emitter"):
            return
        self._remove_all()

    def _remove_all(self) -> None:
        removed = self._entries.update(lambda entries: ((), list(entries)))
        for gone in removed:
            displaced = gone.ref()
            if displaced is not None and isinstance(displaced, Displaceable):
                locked.check("told a receiver it was displaced")
                displaced.displaced()
            del displaced
        del removed

    def compact(self) -> None:
        """Drops the subscriptions whose receivers are gone: what a send does when it finds one."""
        if locked.defer(self._compact, "compacted an emitter"):
            return
        self._compact()

    def _compact(self) -> None:
        snapshot = self._entries._value
        dead = self._gone(snapshot)
        if not dead:
            return
        removed = self._entries.update(
            lambda entries: (tuple(e for e in entries if id(e) not in dead), [e for e in entries if id(e) in dead]))
        del snapshot, removed

    @staticmethod
    def _gone(snapshot: tuple[_Entry, ...]) -> set[int]:
        """The subscriptions in ``snapshot`` whose receivers are gone, read without the lock."""
        return {id(entry) for entry in snapshot if entry.ref() is None}
