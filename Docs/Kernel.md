# The kernel

What a run stands on: one writer for every run's state, events that reach their listeners in order, and a rule about
locks held at run time. GraniteRust's and GraniteC's, in Python's threads.

`src/granite/core/` holds three pieces:

- **`main.py`: the single writer.** `granite.main` is one thread, started on first use, that runs every change to a
  run's record as a turn, in the order sent: start, cancel, drain, settle. A turn never waits. Work that blocks (a
  run, an ask of the model, a child process) runs on a thread of its own and sends its result back. `enqueue` always
  defers; `run` is inline on main; `call` hands back a turn's value or exception.
- **`emitter.py`: values sent to receivers held weakly.** `Emitter.on(receiver, handler, key=None)` keeps the receiver
  by `weakref` and hands it to the handler on every send, so the subscription ends when the receiver is released:
  no tokens, nothing torn down in `__del__`. A key replaces the subscription under it and moves it last; the receiver
  it displaced is told (`Displaceable.displaced`). Delivery is synchronous, on the sender's thread, with the lock
  released. A handler bound to its own receiver, or closing over it, is refused: it would keep the receiver alive.
- **`locked.py`: state behind a lock.** `Locked.with_(body)` runs a body that reads and changes the value and
  returns what it removed, which is dropped after the unlock.

## The rule, held at run time

One lock at a time, and nothing called under it. A thread that takes a second `Locked` while holding one, or reaches
the author's code (a run, a compute, a model ask) while holding one, raises `LockRuleError` naming both. Swift's
compiler and Rust's borrow checker hold most of this; here, as in GraniteC, it is checked as it happens.

Python adds one case the others do not have: the cycle collector can run any object's `__del__` on whichever thread
allocates, even one inside a locked body. What such a finalizer asks of the kernel (an emitter's `on`, `off`, `send`)
is deferred until the thread lets its lock go. In strict mode (`locked.strict`, set by the tests) it is recorded in
`locked.violations` and raised instead, so a test sees the kernel broke its own rule.

The emitter keeps its subscriptions as an immutable tuple swapped whole under the lock. A send reads it without the
lock; which receivers are gone is read before the lock is taken, since loading a weak reference makes this thread an
owner that may be the last.

## Runs

`runs.py` is the engine behind a package's calls (GraniteRust's RunContainer). A call is performed with a key; the
engine starts it on a thread (or queues it for a lane), and the caller blocks until it settles:

- a finished run settles in the drain that read its end, after its last event reached the taps;
- a cancel settles at once, as `Cancelled`; whatever the run's thread delivers after is dropped;
- the run's own refusal keeps its kind; anything else it raised is a fault said as it is; a compute that fails is
  the engine's sentence, `PackageRun-N failed: …`;
- the same key asked again replaces the run before it.

## Cancelling

Cooperative. A run is never killed: its `CancelFlag` is set, an ask of the host's model it is blocked in wakes ("the
call was cancelled"), an async run's task is cancelled, and the run is expected to return. A runner process is
another matter: the host terminates it after a grace ([Hosting.md](Hosting.md)).
