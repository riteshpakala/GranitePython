# Provenance

The golden corpora GranitePython is held to, copied from [GraniteC](https://github.com/riteshpakala/GraniteC) at
`f5a6e98`, where each was produced by the tool it stands for, never written by hand:

| Folder | Cases | What made the expected file |
| --- | --- | --- |
| `json/` | `corpus.hex`, 935 JSON texts, hex | serde_json 1.0.154 (GraniteRust's): `ok <compact> <pretty>` or `err <sentence>` |
| `wire/` | `corpus.hex`, 79 lines, hex | GraniteRust's `AgentWire::read`: `ok <line written back>` or `err <sentence>` |
| `check/` | `cases.jsonl`, 59 instances | GraniteRust's `granite check`: exit code and stderr |
| `cli/` | `cases.jsonl`, 30 commands | GraniteRust's `granite`, normalized by `normalize.py` (pids, costs, places) |

`parrot.describe` is GraniteRust's parrot runner's `--describe` line, byte for byte. To regenerate any of them,
run GraniteC's `scripts/*-goldens.sh` and copy the files here again.
