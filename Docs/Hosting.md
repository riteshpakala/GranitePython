# Hosting agents

The host's side is the `granite` command and the library behind it (`granite.host`): one runner process per call, a
shelf of installed runners, and an installer for Python agent repositories. Its usage, outputs and exit codes are
GraniteRust's `granite`, byte for byte (`tests/test_cli.py` holds them to Rust's 30 cases).

```text
granite agents add <name | git URL[@revision] | folder> --shelf <dir>
granite agents update <name> --shelf <dir>
granite agents rm <name> --shelf <dir>
granite agents list --shelf <dir> [--json]
granite describe <runner>
granite run <runner> <perform.json | -> [--directory <dir>] [--ask <text>] [--word <word>]
granite check --schemas <dir>... --schema <file or $id> <instance.json | ->
```

`pip install .` puts `granite` on the path; `python3 scripts/build-cli.py` writes it as one file (`build/granite`)
stamped with its commit, as the container image does. A shelf is `--shelf` or `GRANITE_SHELF`, never a default. `run`
is a host with no model: it prints one line, `{"output": …}` or `{"failed": …, "refused": …}` with the runner's
`status`, the cost on stderr, and exits 0 answered, 3 refused, 1 failed, 2 when the runner broke the wire or died.
SIGINT or SIGTERM during a run cancels the runner, reaps it, and ends the command by the same signal.

## A call

`Process(executable).perform(word, input, host, cancel)` starts the runner and:

1. starts it clean: its own process group, an empty signal mask, TERM, INT, HUP, QUIT and PIPE at their defaults,
   no inherited descriptor, `GRANITE_AGENT=1`, the host's folder as its working folder. This is libc's
   `posix_spawn`, called by `ctypes` (`POSIX_SPAWN_CLOEXEC_DEFAULT` on Apple; every inheritable descriptor closed
   elsewhere), because `subprocess` passes a blocked SIGTERM and an ignored SIGINT through. Without `ctypes`,
   `os.posix_spawn` does the same, and a small trampoline sets the folder;
2. waits for `hello` of major 1, sends the call once, relays notes to `host.note`, and answers each ask of the model
   on a thread of its own (a host without one says "this host has no model loaded");
3. reads the one `output` or `failed`: refused (with its kind), a fault (the runner named the answer standard), or a
   plain failure; a broken wire is unreadable; a runner that ends without answering is ended, with its status and
   the last six lines it printed off the wire;
4. closes its stdin, terminates it if it is still there five seconds later (SIGTERM to its group, SIGKILL two seconds
   after), reaps it with `wait4` for its cost, and kills whatever is left of its group.

A cancel says `cancel` and terminates the runner after two seconds. `GRANITE_AGENT_TRACE=1` traces every line either
side sends or reads, to stderr. A `RemoteError` says each ending as Rust does, with the call's `cost`.

## The runner

`granite.serve(roster)` is a runner's main. `--describe` says the roster's entries and exits. Otherwise it sets the
wire aside (stdout's descriptor, close-on-exec, so children do not inherit it), makes stdout stderr (`print` stays in
order and off the wire), puts `/dev/null` on stdin, says `hello`, answers one call, and leaves through `os._exit` once
its last line is written: 0 answered, 1 failed, 2 the host spoke out of turn or left, 3 refused, 130 cancelled.
SIGTERM cancels the call through a wakeup pipe read on a thread of its own, so no signal is left blocked for an
agent's children to inherit.

## The shelf

One folder per installation, each with its runner (`agent`) and `installation.json`, the record GraniteRust and
GraniteC write: each reads the others' shelves (interop holds it). A runner whose size or modification time moved is
digested before a call, and one whose SHA-256 is not the recorded one is refused: install it again. Only `agents add`
writes to a shelf.

## The installer

```text
fetch → pyproject.toml → bundle → <runner> --describe → install
```

The stages are Swift's words (fetching, reading the package, building, asking … what it carries, installing,
installed). Building is bundling: the repository's `source` folder and the installer's own `granite` package are
written into one zipapp with a generated `__main__.py`, sorted, dated 1980-01-01, stored, a `.pyc` (unchecked hash)
beside each `.py`, so two installs of one commit give the same bytes. Its first line names the Python that ran the
installer, with `-I`, so `PYTHONPATH`, the user's site and the working folder cannot reach the runner's code; an
interpreter whose path has a space cannot be named there and is refused. The record's `granite` is GranitePython's
version and commit (`0.1.0@221ef85763b1`), and `sha256` covers all the runner's code.

Refused, each in its sentence: a folder without `pyproject.toml` (one with a `Cargo.toml`, `granite-agent.json` or
`Package.swift` is pointed at the host that installs it), no `[tool.granite]`, a roster that is not `module:attribute`
or not in `source`, a newer GranitePython, a `requires-python` this Python does not keep, third-party `dependencies`,
source that does not compile (its log kept beside the shelf), a runner that describes nothing (its traceback said),
and a word another installation carries. One word is one installation; installing the same repository again replaces
it whole (staged, then renamed).

## granite check

The keywords the standard's schemas use (`$ref`, `$defs`, `$id`, `type`, `properties`, `required`, `items`,
`additionalProperties`, `oneOf`, `allOf`, `if`/`then`/`else`, `enum`, `const`, `pattern`, `minimum`, and the
annotations), each problem said as jsonschema-rs says it at its JSON pointer (`tests/test_jsonschema.py` holds 59 of
Rust's cases). A census fails if a schema reaches past them.
