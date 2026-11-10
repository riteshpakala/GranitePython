# GranitePython

Granite's agent kernel and the `granite-agent/1` standard in Python, standard library only: what you import to write
an agent in Python, and the `granite` command a host uses to install, describe, run and check one, in its own
container or on the machine. The Swift original is [Granite](https://github.com/riteshpakala/Granite); the other ports
are [GraniteRust](https://github.com/riteshpakala/GraniteRust) and [GraniteC](https://github.com/riteshpakala/GraniteC).
The wire is language-neutral and the bytes are the same, so any of their hosts drives any of their runners.

```sh
scripts/check.sh        # every file compiled with warnings as errors, and every test, on Python 3.11 and 3.12
```

Needs Python 3.11 or later; nothing else. [PORTING.md](PORTING.md) maps each Swift and Rust file to its Python home.
