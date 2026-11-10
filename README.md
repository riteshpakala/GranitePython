# GranitePython

Granite's agent kernel and the `granite-agent/1` standard in Python, standard library only: what you import to write
an agent in Python, and the `granite` command a host uses to install, describe, run and check one, in a container or
on the machine. The Swift original is [Granite](https://github.com/riteshpakala/Granite); the other ports are
[GraniteRust](https://github.com/riteshpakala/GraniteRust) and [GraniteC](https://github.com/riteshpakala/GraniteC).
The wire is language-neutral and the bytes are the same, so any of their hosts drives any of their runners
(`scripts/interop.sh` holds the nine pairings to Rust's bytes).

```python
from dataclasses import dataclass

import granite


@dataclass
class Input:
    text: str = granite.field(note="what to say")


class Echo(granite.AgentPackage):
    word = "echo"
    summary = "Says it back."
    Input = Input

    def run(self, input: Input, context: granite.Context) -> None:
        context.note("saying it back")
        context.deliver(granite.Answer(said=input.text))


roster = granite.Roster(Echo)

if __name__ == "__main__":
    granite.serve(roster)
```

```sh
granite agents add ./echo-agent --shelf ~/agents        # one file: the runner, a zipapp
echo '{"word":"echo","input":{"text":"hi"}}' | granite run ~/agents/EchoAgent/agent -
```

- [Docs/Agents.md](Docs/Agents.md): writing an agent: the package, its input read off a dataclass, the run, the
  answer, asking the host's model, refusing, and the repository.
- [Docs/Hosting.md](Docs/Hosting.md): the `granite` command, a call, the runner, the shelf, the installer.
- [Docs/Kernel.md](Docs/Kernel.md): the single writer, the emitter, the lock rule held at run time, runs and cancels.
- [Docs/Containers.md](Docs/Containers.md): the checks in Apple's `container`, and what has and has not run.
- [Docs/Deviations.md](Docs/Deviations.md): every place GranitePython differs from Rust, and why.
- [PORTING.md](PORTING.md): each Swift, Rust and C file's Python home.

## Building

Python 3.11 or later; nothing else.

```sh
pip install .                          # the granite package and the granite command
python3 scripts/build-cli.py           # or the command as one file: build/granite
scripts/check.sh                       # every file compiled with warnings as errors, and every test, on 3.11 and 3.12
containers/check.sh <agent> [--native] # an agent repository installed and called in Apple's container
scripts/conformance-linux.sh           # this suite on Linux arm64, in Apple's container
scripts/interop.sh <GraniteRust> <GraniteC>  # three hosts, three runners, Rust's bytes
```

GPL-3.0; see [LICENSE](LICENSE).
