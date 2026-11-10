# Porting

GranitePython is Granite's agent kernel and the `granite-agent/1` standard in Python, ported from the Swift sources
(`riteshpakala/Granite`), the Rust port (`riteshpakala/GraniteRust`) and the C port (`riteshpakala/GraniteC`). Like
GraniteC, it carries what an author needs to write an agent and what a host needs to install, describe, call and check
one; Granite's app architecture (centers, reducers, services, tickets) is Swift's and Rust's. This file maps every file
to its Python home, or says it was not ported, as each lands.

| Part | What it is | Status |
| --- | --- | --- |
| `Schema/` | the standard and its four JSON Schemas, verbatim | copied |
| `tests/goldens/` | the JSON, wire, check and CLI corpora, from serde_json and GraniteRust | copied |
| `src/granite/core/` | the lock and its rule, the emitter, granite.main | to do |
| `src/granite/` | JSON, errors, contexts, the run engine, packages, the wire, the runner | to do |
| `src/granite/host/` | the host's side of a call, the shelf, the installer | to do |
| `src/granite/jsonschema.py`, `cli.py` | `granite check`, the `granite` command | to do |
