# Writing an agent in Python

An agent is a package: a word a host calls it by, a sentence saying what it does, an input it takes, and a run that
streams events and ends in an answer. A runner is a process that carries one or more packages and speaks
`granite-agent/1` on its stdin and stdout. Any Granite host (Swift's, Rust's, C's or Python's) installs and calls it
the same way.

```python
from dataclasses import dataclass

import granite


@dataclass
class Input:
    text: str = granite.field(note="what to say")
    times: granite.u32 | None = None


class Echo(granite.AgentPackage):
    word = "echo"
    summary = "Says it back."
    guide = "Give it text; it says it back."
    Input = Input

    def run(self, input: Input, context: granite.Context) -> None:
        context.note("saying it back")
        context.deliver(granite.Answer(said=input.text * (input.times or 1)))


roster = granite.Roster(Echo)

if __name__ == "__main__":
    granite.serve(roster)
```

## The package

A subclass of `granite.AgentPackage`, said in class attributes:

| Attribute | What it says | Default |
| --- | --- | --- |
| `word` | the word a host calls it by: one lowercase word, `[a-z][a-z0-9-]*`, unique among what a host runs | required |
| `summary` | what it does, one sentence a host shows a person or a model | required |
| `guide` | how to use it well, in its own words | none |
| `Input` | a dataclass of what it takes (below), or None for any JSON | None |
| `makes` | what it makes: `granite.Make(kind, what, into, never)`, one per kind | none |
| `lanes` | how many of its calls run at once in one process: 0 for two, `granite.UNBOUNDED` for no bound | 0 |
| `module`, `type` | the names a host shows beside the word | the class's top module, `module.Class` |

`granite.Roster(*packages)` holds them in order (the first package of a word wins), and refuses a word that is not
one, a missing summary, or an input type it cannot read, when the roster is made rather than on the first call.

### The input

The schema is read off the dataclass, field by field, in declaration order:

| Annotation | Schema | serde's word when it does not fit |
| --- | --- | --- |
| `str` | `string` | a string |
| `bool` | `boolean` | a boolean |
| `int` (`granite.u32`, `granite.u64`, `granite.i32`) | `integer` | i64 (u32, u64, i32) |
| `float` | `number` (an integer reads too) | f64 |
| `Literal["a", "b"]` | `{"choice": ["a", "b"]}` | string or map; unknown variant |
| `list[T]` | `{"array": T}` | a sequence |
| another dataclass | `{"object": [...]}` | struct Name |
| `Any`, `dict` | `any` | — |
| any of those `\| None` | the same, not required | null reads as None |

A field is required unless it is `| None`; a default does not make it optional, as in Rust. Its wire name is its
attribute's in camelCase (`ask_model` reads `askModel`), as Rust's `rename_all = "camelCase"`;
`granite.field(name=..., note=...)` gives another name and the line a host shows beside the field (`fields`, and
`description` in the JSON Schema a model is shown). A field named `directory` is the host's own: the folder the call
works in, never shown to a model.

Input that does not read is refused as kind `input` before the run starts, in serde's words:
`echo could not read its input: missing field `text``.

### The run

`run(self, input, context)` runs on a thread of its own and blocks; an `async def run` runs in an event loop of its
own on that thread. It:

- streams with `context.deliver(event)`: a dict, list, string, number, boolean, None, or a dataclass (by its wire
  names). The event is copied as plain JSON then and there, so a later change to it is not seen, and one that is not
  JSON raises `TypeError` in the run;
- says progress with `context.note(line)`, shown by the host as the agent's work;
- checks `context.is_cancelled` (or waits on `context.cancel`) and returns soon after; an async run's task is
  cancelled for it. A cancelled run's later events are dropped;
- reads `context.directory` and `context.ask` (the person's words), either of which a host may not offer, and
  `context.compute`: what `compute(self)` made on the package's first call in this process, shared by every run.

The answer is read off the events by `output(self, events)`, the last by default. An answer is a
`granite.Answer`: a dict with `path`, `paths`, `made`, `expected`, `problems` and `payloads` to start, the package's
own keys beside them (as keywords, a dict, or a dataclass, flattened as Rust's `Answer { own, .. }`):

```python
answer = granite.Answer(table=table)
answer.path("blackjack/table.png").made("table", "the table", "blackjack/table.png").note("dealt")
context.deliver(answer)
```

It must keep `granite-answer/1`: every file it wrote in `paths` (`[]` when none), `made` of kinds it declares in
`makes`, each `payloads` item a JSON file whose own `schema` says what it is. One that does not is refused as kind
`answer`, every problem listed; the runner stamps `format` and `agent`.

### Asking the host's model

```python
answer = context.ask_object(schema, "Count.", "How many?", max_tokens=16, seconds=5)  # JSON that fits a JSON Schema
reply = context.ask_text("Be brief.", "Name a colour.")
plan = context.ask_as(Plan, "Plan it.", context.ask or "")                             # a dataclass, decoded
```

Each blocks until the host's model answers, gives up, or the call is cancelled. A host without a model, no answer, or
an answer that does not decode are each a refusal of kind `model`, in Rust's sentence: let it go up.

### Saying no

`raise granite.Refusal(kind, reason)` refuses the call. The kind says whose it is to fix (`input`, `call`, `build`,
`device`, `model`, `parse`, `reach`, `answer`, `host`); the reason says the fix. Several reasons read as a count and
a list; `said=` gives the refusal its own sentence. Anything else raised is the agent's own fault, said as
`str(error)`.

## The repository

```text
pyproject.toml   [project] with no dependencies, [tool.granite]
src/<module>/    the agent's packages (any files beside the .py are bundled too)
Schema/examples/ the calls containers/check.sh makes: *.perform.json and *.refusal.json
```

```toml
[project]
name = "echo-agent"
requires-python = ">=3.11"
dependencies = []

[tool.granite]
name = "EchoAgent"          # the installation's folder on a shelf
agent = "echo_agent:roster" # module:attribute of the roster
granite = "0.1.0"           # the least GranitePython it needs
source = "src"              # the folder whose packages are bundled
```

`granite agents add <folder | git URL[@revision]> --shelf <dir>` bundles `source` and GranitePython's own package
into one file, the runner ([Hosting.md](Hosting.md)). It carries the standard library and GranitePython only: a
repository with third-party `dependencies` is refused, with a sentence, until bundling them lands.

## In one process

`granite.perform(package, input_bytes, host=None, cancel=None)` makes a call without a runner: the same pipeline, the
answer's canonical bytes back, or the refusal raised. `granite.perform_value(package, {...})` takes and gives JSON
values. A test calls its packages so, and drives the runner through `granite run` once it is installed.
