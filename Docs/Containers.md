# Containers

Three checks run in Apple's [`container`](https://github.com/apple/container) (Linux, arm64), CPU only:

| Script | What it runs | Image |
| --- | --- | --- |
| `scripts/conformance-linux.sh` | this suite (`scripts/check.sh`) on Debian's Python 3.11 and the image's 3.12 | `containers/Dockerfile.linux` |
| `containers/check.sh <agent>` | an agent repository installed, described and called as a host would | `containers/Dockerfile` |
| `scripts/container.sh <granite args>` | the `granite` command in the check image, this folder at `/scratch` | `containers/Dockerfile` |

Both images are `python:3.12-slim-bookworm` with git. The check image builds the `granite` command as one file
(`scripts/build-cli.py`) at `/usr/local/bin/granite`, stamped with the checkout's commit, and the standard's schemas
and Craft's output schema at `/usr/local/share/granitepython/schemas`. Runners it installs name
`/usr/local/bin/python3`.

`containers/check.sh <agent folder | git URL[@revision]> [--native] [--out <dir>] [--keep]`:

1. installs the agent with GranitePython's installer and checks its `installation.json` against
   `granite-agent-installation.schema.json`;
2. describes it, every entry against `granite-agent-entry.schema.json`;
3. makes every call in its `Schema/examples/`, in name order, in one folder: `*.perform.json` must answer (the answer
   against Granite's `granite-agent-answer.schema.json` and Craft's `craft-agent-output.schema.json`, every path
   there, `made` of kinds it declares, every `expected` made), and `*.refusal.json` must refuse with its kind.

`--native` does the same on this machine with a `granite` built into a scratch folder. `GRANITE_CHECK_MEMORY` (4g)
and `GRANITE_CHECK_CPUS` (4) size each container.

The container system starts as GraniteRust's Docs/Containers.md says, with the kernel fetched on first start:

```sh
~/.local/apple-container/1.6.1/bin/container system start \
    --install-root ~/.local/apple-container/1.6.1 --enable-kernel-install
```

## What has run, and what has not

Run (2026-11-10, Apple container 1.6.1 on macOS 27, arm64):

- `scripts/conformance-linux.sh`: 102 tests on Python 3.11.2 (Debian) and 3.12.16, glibc 2.36: `posix_spawn` by
  `ctypes` with `posix_spawn_file_actions_addchdir_np`, descriptors read from `/proc`, `wait4`'s kilobytes.
- `containers/check.sh tests/fixtures/agent-repo`, in the container and `--native`: the parrot installed, its record
  and entry valid, one answer and two refusals as declared.
- `scripts/interop.sh` (natively): Rust's, C's and Python's hosts and runners, nine ways, the same bytes.

Not run: Craft itself, and x86_64 Linux.
