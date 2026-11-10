# Porting

GranitePython is Granite's agent kernel and the `granite-agent/1` standard in Python, ported from the Swift sources
(`riteshpakala/Granite`), the Rust port (`riteshpakala/GraniteRust`) and the C port (`riteshpakala/GraniteC`). Like
GraniteC, it carries what an author needs to write an agent and what a host needs to install, describe, call and
check one; Granite's app architecture (centers, reducers, services, tickets) is Swift's and Rust's. This maps each
part to its Python home, or says it was not ported. [Docs/Deviations.md](Docs/Deviations.md) says how each differs.

## Granite's core (Swift `Architecture/Core`, Rust `crates/granite/src`, C `core/`)

| Swift / Rust | Python | Notes |
| --- | --- | --- |
| `GraniteLock`, `Locked<T>` | `granite/core/locked.py` | the rule held at run time; finalizers deferred |
| `GraniteEmitter`, `Emitter<T>` | `granite/core/emitter.py` | `weakref` receivers; Swift's EmitterTests ported |
| the main actor, `Main` | `granite/core/main.py` | the `granite.main` thread |
| Center, reducers, effects, services, relays, tickets, persistence | not ported | Deviation 4 |

## GraniteAgents (Swift `Sources/GraniteAgents`, Rust `crates/granite-agents/src`, C `agents/`)

| Swift / Rust | Python | Notes |
| --- | --- | --- |
| `json` (serde_json) | `granite/agentjson.py` | 935 corpus cases, serde's bytes |
| `AgentRefusal`, `SaidRefusal`, `AgentPackageError` | `granite/error.py` | |
| `CancelFlag`, `AgentHost`, `AgentHostModel`, `AgentContext` | `granite/context.py` | |
| `RunContainer` | `granite/runs.py` | parallel lanes only |
| `GraniteSchema`, typed deserialization | `granite/schema.py` | read off a dataclass |
| `Answer<T>`, `AnswerCheck` | `granite/answer.py` | rule for rule |
| `AgentWire` | `granite/wire.py` | 79 corpus lines |
| `AgentPackage`, `AnyAgentPackage::perform`, `Roster` | `granite/package.py` | the pipeline step for step |
| `PipeChannel` | `granite/channel.py` | |
| `AgentServer`, the runner's main | `granite/server.py` | `granite.serve` |
| `granite_conformance::Parrot` | `tests/fixtures/agent-repo` | its `--describe` is Rust's bytes |
| workers, tools, budgets, shells (`kind: worker`) | not ported | read on the wire, never served |

## The host (Rust `crates/granite-agents/src/{process,install,shelf}`, C `host/`)

| Swift / Rust | Python | Notes |
| --- | --- | --- |
| `AgentSource` | `granite/host/source.py` | |
| `AgentInstallation` | `granite/host/installation.py` | read in serde's order |
| `AgentShelf` | `granite/host/shelf.py` | each port reads the others' shelves |
| `RemoteAgentError` | `granite/host/remote.py` | |
| the child process | `granite/host/child.py` | libc's `posix_spawn` by `ctypes` |
| `RemoteAgentProcess` | `granite/host/process.py` | |
| `AgentInstaller` | `granite/host/install.py`, `command.py` | Python repositories, one zipapp |

## The command, the checks, the containers

| Rust / C | Python | Notes |
| --- | --- | --- |
| `granite-cli` | `granite/cli.py`, `scripts/build-cli.py` | 30 corpus cases |
| `granite check` (jsonschema-rs) | `granite/jsonschema.py` | 59 corpus cases |
| `containers/check.sh` | `containers/check.sh` + `check.py` | Python's installer |
| `scripts/conformance-linux.sh` | the same | Debian's 3.11 and the image's 3.12 |
| `scripts/interop.sh` | the same | three hosts, three runners |
| the golden generators | GraniteC's `scripts/*-goldens.sh` | the corpora are copied ([tests/goldens/PROVENANCE.md](tests/goldens/PROVENANCE.md)) |
