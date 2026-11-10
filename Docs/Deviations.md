# Deviations

Each is deliberate. Where the Swift code and `Schema/GraniteAgent.md` disagree, GranitePython follows the standard,
as GraniteRust does; where GraniteRust deviates from Swift, GranitePython follows GraniteRust unless it says
otherwise here. Several are GraniteC's too, and say so.

1. **The schema is read off a dataclass.** Closer to Swift and Rust than C's declared schema: a field's annotation
   says its shape, its wire name is its attribute's in camelCase, and `granite.u32` and its kin say Rust's integer
   widths, so a Python input refuses what the Rust struct refuses. A field is required unless it is `| None`. A
   struct reads from a map only; serde_json would also read one from a sequence.
2. **Input that does not fit is said without serde's position** ("missing field `text`", where Rust's typed
   deserializer adds "at line 1 column 2"), as in GraniteC. JSON that does not parse is said as serde says it,
   position included. Interop compares the rest of such a call.
3. **Events and answers are JSON.** A run delivers dicts, lists and dataclasses, copied as plain JSON on delivery;
   `output` reads the answer off them, the last event by default. `granite.Answer` writes what Rust's `Answer<T>`
   writes.
4. **Threads, one concurrency policy, cooperative cancel.** As GraniteC: every call has its own key, so the engine is
   parallel up to the package's lanes. A run is a blocking function on its own thread (or an `async def` in a loop of
   its own there), and a cancel sets its flag, wakes its model ask and cancels its task; it is never killed. Not
   ported: supersede, drop, merge, coalescing, waits by prefix, `RunScope`, child runs, a resolver per input, and
   Granite's app architecture (centers, reducers, effects, services, relays, tickets, persistence).
5. **The lock rule is held at run time**, as in GraniteC: a second lock, or the author's code, under a lock raises
   `LockRuleError`. Python adds a finalizer run by the cycle collector under a lock: what it asks of the kernel is
   deferred until the lock is let go ([Kernel.md](Kernel.md)).
6. **The wire is the runner's alone**, as in GraniteC: its descriptor is close-on-exec, stdin is `/dev/null`, and
   `sys.stdout` is stderr, so a `print` stays off the wire.
7. **SIGTERM is caught through a wakeup pipe**, not blocked and not a plain handler: a blocked signal would be
   inherited by an agent's children, and a Python handler waits for the main thread.
8. **A runner leaves through `os._exit`** once its last line is written and stderr flushed: no thread still running
   can write after it, and no interpreter shutdown can hang it.
9. **A call's processes go with it**, as in GraniteC: a clean start (above) by libc's `posix_spawn` through `ctypes`,
   and whatever is left of the runner's group killed once it is reaped. Rust's leaves a grandchild running.
10. **An interrupted `granite run` ends its runner**, as in GraniteC, then ends by the same signal.
11. **A runner is one zipapp tied to the Python that installed it.** Its first line names that interpreter with `-I`;
    moving or removing that Python breaks the runner, and installing again mends it. A `.pyc` compiled for another
    Python falls back to the source.
12. **Standard library only, for now.** A repository with third-party `dependencies` is refused with a sentence;
    nothing is fetched from an index.
13. **Python agent repositories.** The installer reads `pyproject.toml`'s `[tool.granite]` where Rust's reads Cargo
    metadata; its sentences say Python's words ("… is not a Python agent repository …", "… does not build against
    GranitePython: it needs GranitePython 9.0, and this is 0.1.0"), and point a Cargo, C or Swift repository at the
    host that installs it.
14. **JSON read by hand.** The standard `json` module accepts `NaN`, keeps lone surrogates and reads `-0` as an
    integer, so GranitePython reads bytes with its own reader: serde's sentences and positions, depth 128, and serde's
    inexact float path, which differs from `float(text)` in about one case in nine (935 of 935 corpus cases, and
    interop's mirror, exact). A lone surrogate written out becomes U+FFFD.
15. **Records read without positions**, as in GraniteC, but in serde's order: sorted keys, then the first required
    field missing.
16. **`granite check` reads a subset**, as in GraniteC: the keywords the standard's schemas use, with jsonschema-rs's
    sentences and order, and Python's `re.search` for `pattern`.
17. **The scripted test runner cannot see SIGPIPE's inherited disposition**, since Python ignores SIGPIPE as it starts:
    it reports the other four, and interop has the Python host start GraniteC's scripted runner, which sees all five.
