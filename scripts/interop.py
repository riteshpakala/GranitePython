#!/usr/bin/env python3
"""GranitePython against GraniteRust and GraniteC, natively: every host with every runner, the same bytes.

  1. describe: Rust's, C's and Python's granite, each describing Rust's, C's and Python's parrot: one output.
  2. run: every case, through each host and each runner, against Rust's granite with Rust's parrot: the same line,
     status, notes and exit code (pids and costs made the same). A case whose input does not read differs only in
     serde's typed position, which C and Python do not say (Docs/Deviations.md).
  3. trace: what each host sends a runner (GRANITE_AGENT_TRACE=1), byte for byte.
  4. shelves: Rust's granite checks and lists a shelf Python's installer wrote, and C's lists it; Python's granite
     lists shelves C's and Rust's installers wrote (Rust's hello agent, built in release: a minute), and drives
     Rust's hello agent as Rust's granite does.
  5. scripted: Python's host starts GraniteC's scripted runner clean: no signal blocked or ignored (SIGPIPE among
     them, which a Python runner cannot see), no descriptor inherited, its own group.
  6. mirror: every value of the JSON corpus through a Python runner (tests/fixtures/mirror_runner.py), equal to what
     serde_json writes of the same text, read and written three times as a runner does.

  scripts/interop.sh <GraniteRust checkout> <GraniteC checkout> [--quick]   (--quick skips step 4's Rust build)
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

rust, c = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
quick = "--quick" in sys.argv
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
build = os.path.join(c, "build")
problems = []


def say(text):
    print(text, flush=True)


say("building GraniteRust's granite and parrot, GraniteC's build, and Python's granite and parrot")
subprocess.run([cargo, "build", "-q", "-p", "granite-cli", "-p", "granite-conformance", "--bin", "parrot-runner"],
               cwd=rust, check=True)
# GraniteC's CLI, fixtures and uninstalled granitec.pc: what its own interop target builds.
subprocess.run(["make", "-s", "-C", c, "all", f"{build}/fixtures/parrot_runner", f"{build}/fixtures/mirror_runner",
                f"{build}/fixtures/scripted_runner", f"{build}/pkgconfig/granitec-uninstalled.pc"], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
work = os.path.realpath(tempfile.mkdtemp(prefix="granitepython-interop."))
environment = {k: v for k, v in os.environ.items() if k not in ("GRANITE_SHELF", "GRANITE_AGENT_TRACE")}
# Rust's installer builds with cargo: its folder on PATH, as a shell with rustup has it.
environment["PATH"] = os.path.dirname(cargo) + os.pathsep + environment.get("PATH", "")
python_cli = os.path.join(work, "granite")
subprocess.run([sys.executable, os.path.join(root, "scripts", "build-cli.py"), "--out", python_cli, "--python",
                sys.executable], check=True, stdout=subprocess.DEVNULL)
python_shelf = os.path.join(work, "python-shelf")
subprocess.run([python_cli, "agents", "add", os.path.join(root, "tests", "fixtures", "agent-repo"), "--shelf",
                python_shelf], check=True, capture_output=True)
hosts = {"rust": f"{rust}/target/debug/granite", "c": f"{build}/bin/granite", "python": python_cli}
runners = {"rust": f"{rust}/target/debug/parrot-runner", "c": f"{build}/fixtures/parrot_runner",
           "python": f"{python_shelf}/parrot-python/agent"}


def normalize(text):
    for path in runners.values():
        text = text.replace(path, "{PARROT}")
    text = text.replace(work, "{WORK}")
    text = re.sub(r'"pid":\d+', '"pid":1', text)
    text = re.sub(r"as pid \d+", "as pid 1", text)
    text = re.sub(r"\[granite-agent \d+\]", "[granite-agent 1]", text)
    return re.sub(r"released · peak [0-9.]+ [MG]B · [0-9.]+ s CPU in [0-9.]+ s", "released · <cost>", text)


def granite(host, args, stdin="", extra=None):
    ran = subprocess.run([hosts[host]] + args, input=stdin, cwd=work, capture_output=True, text=True,
                         env=dict(environment, **(extra or {})))
    return ran.returncode, normalize(ran.stdout), normalize(ran.stderr)


# 1. describe ---------------------------------------------------------------------------------------------------------
say("1. describe")
reference = granite("rust", ["describe", runners["rust"]])
for host in hosts:
    for runner in runners:
        said = granite(host, ["describe", runners[runner]])
        if said != reference:
            problems.append(f"describe: {host} host, {runner} runner: {said} != {reference}")
say(f"   nine ways, one output ({len(reference[1])} bytes)")

# 2. run --------------------------------------------------------------------------------------------------------------
say("2. run")
cases = [
    ({"word": "parrot", "input": {"text": "hi", "notes": 2}, "ask": "say", "directory": "/tmp"}, False),
    ({"word": "parrot", "input": {"text": "x", "fail": True}}, False),
    ({"word": "parrot", "input": {"text": "x", "fault": True}}, False),
    ({"word": "parrot", "input": {"text": "x", "crash": True}}, False),
    ({"word": "parrot", "input": {"text": "x", "askModel": True}}, False),
    ({"word": "parrot", "input": {"text": "x", "delay": 0.2}}, False),
    ({"word": "comic", "input": {}}, False),
    ({"word": "parrot", "input": {"text": 'é "quoted" / slashed', "directory": "rel"}}, False),
    ({"word": "parrot", "input": {}}, True),
    ({"word": "parrot", "input": {"text": 5}}, True),
    ({"word": "parrot", "input": {"text": "x", "notes": -1}}, True),
    ({"word": "parrot"}, True),
]
# C's parrot declares `notes` an integer of no width (GraniteC's Deviation 2), so it reads -1 where Rust and Python
# refuse it: its runner is not compared on that case.
not_compared = {json.dumps({"word": "parrot", "input": {"text": "x", "notes": -1}}): {"c"}}
for perform, typed in cases:
    stdin = json.dumps({"perform": perform})
    reference = granite("rust", ["run", runners["rust"], "-"], stdin)
    for host in hosts:
        for runner in runners:
            if runner in not_compared.get(json.dumps(perform), ()):
                continue
            said = granite(host, ["run", runners[runner], "-"], stdin)
            if said == reference:
                continue
            if typed and runner in ("c", "python"):
                # serde's typed sentence ("… at line 1 column 2") is Rust's own: compare the rest.
                line, rust_line = json.loads(said[1]), json.loads(reference[1])
                if runner == "python" and line.get("failed", "") == re.sub(r" at line \d+ column \d+$", "",
                                                                           rust_line.get("failed", "")):
                    continue
                same = (said[0], line.get("refused", {}).get("kind"), line.get("status")) == \
                       (reference[0], rust_line.get("refused", {}).get("kind"), rust_line.get("status"))
                if same and runner == "c":
                    continue
            problems.append(f"run {perform}: {host} host, {runner} runner:\n  {said}\n  != {reference}")
say(f"   {len(cases)} cases, through each host and each runner")

# 3. trace ------------------------------------------------------------------------------------------------------------
say("3. trace")
stdin = json.dumps({"perform": {"word": "parrot", "input": {"text": "traced"}, "ask": "a/b", "directory": "/tmp"}})
for runner in runners:
    traces = {}
    for host in hosts:
        _, _, err = granite(host, ["run", runners[runner], "-"], stdin, {"GRANITE_AGENT_TRACE": "1"})
        traces[host] = [line for line in err.splitlines() if "] sent " in line or "] read " in line]
    for host in ("c", "python"):
        if traces[host] != traces["rust"]:
            problems.append(f"trace with {runner}'s runner: {host} {traces[host]} != rust {traces['rust']}")
say(f"   {len(traces['rust'])} lines, the same from each host with each runner")

# 4. shelves ----------------------------------------------------------------------------------------------------------
say("4. shelves")
code, out, err = granite("rust", ["check", "--schemas", f"{root}/Schema", "--schema",
                                  "granite-agent-installation.schema.json",
                                  f"{python_shelf}/parrot-python/installation.json"])
if code != 0:
    problems.append(f"Rust checks Python's installation.json: {err}")
for flags in ([], ["--json"]):
    listed = {host: granite(host, ["agents", "list", "--shelf", python_shelf] + flags) for host in hosts}
    for host in ("c", "python"):
        if listed[host] != listed["rust"]:
            problems.append(f"list of Python's shelf {flags} by {host}: {listed[host]} != {listed['rust']}")
c_shelf = f"{work}/c-shelf"
code, _, err = granite("c", ["agents", "add", f"{c}/tests/fixtures/agent-repo", "--shelf", c_shelf],
                       extra={"PKG_CONFIG_PATH": f"{build}/pkgconfig"})
if code != 0:
    problems.append(f"C install: {err}")
for flags in ([], ["--json"]):
    rust_list, python_list = (granite(h, ["agents", "list", "--shelf", c_shelf] + flags) for h in ("rust", "python"))
    if rust_list != python_list:
        problems.append(f"list of C's shelf {flags}: {python_list} != {rust_list}")
if not quick:
    rust_shelf = f"{work}/rust-shelf"
    code, _, err = granite("rust", ["agents", "add", f"{rust}/fixtures/hello-agent", "--shelf", rust_shelf])
    if code != 0:
        problems.append(f"Rust install: {err[-500:]}")
    for flags in ([], ["--json"]):
        rust_list, python_list = (granite(h, ["agents", "list", "--shelf", rust_shelf] + flags)
                                  for h in ("rust", "python"))
        if rust_list != python_list:
            problems.append(f"list of Rust's shelf {flags}: {python_list} != {rust_list}")
    record = f"{rust_shelf}/hello-agent/installation.json"
    if os.path.exists(record):
        word = json.load(open(record))["entries"][0]["word"]
        runner = f"{rust_shelf}/hello-agent/agent"
        for args in (["describe", runner], ["run", runner, "-"]):
            stdin = json.dumps({"word": word, "input": {"name": "interop"}}) if args[0] == "run" else ""
            rust_said, python_said = granite("rust", args, stdin), granite("python", args, stdin)
            if rust_said != python_said:
                problems.append(f"{args[0]} of Rust's hello agent: {python_said} != {rust_said}")
    say("   Rust and C read Python's shelf; Python reads C's and Rust's, and drives Rust's hello agent as Rust does")
else:
    say("   Rust and C read Python's shelf; Python reads C's (Rust's installer skipped: --quick)")

# 5. scripted ---------------------------------------------------------------------------------------------------------
say("5. scripted")
code, out, err = granite("python", ["run", f"{build}/fixtures/scripted_runner", "-", "--directory", work],
                         json.dumps({"word": "x", "input": {}}), {"GRANITE_SCRIPT": "report"})
report = json.loads(out).get("output", {}) if code == 0 else {}
if (report.get("blocked"), report.get("ignored"), report.get("fds"), report.get("leads"), report.get("agent")) != \
        ([], [], [], True, "1") or report.get("cwd") != "{WORK}":
    problems.append(f"Python's host starts C's scripted runner unclean: {code} {out} {err}")
say("   C's scripted runner reports a clean start from Python's host")

# 6. mirror -----------------------------------------------------------------------------------------------------------
# A runner reads its input three times over: the wire line, the input written for the package and read again, and
# the answer written, read and written onto the wire. GraniteRust's runner does the same with serde, whose default
# float reading is not exact, so a double may move at each round: what Python must write is serde's read-and-write
# applied three times (GraniteC's scripts/serde-golden, pinned to Rust's).
say("6. mirror")
corpus = open(f"{root}/tests/goldens/json/corpus.hex").read().split("\n")[:-1]
golden = [cargo, "run", "-q", "--release", "--manifest-path", f"{c}/scripts/serde-golden/Cargo.toml"]
rounds = corpus
for _ in range(3):
    ran = subprocess.run(golden, input="\n".join(rounds) + "\n", capture_output=True, text=True, check=True,
                         env=dict(environment, CARGO_TARGET_DIR=f"{build}/serde-golden"))
    rounds = [line.split(" ")[1] if line.startswith("ok ") else "" for line in ran.stdout.split("\n")[:-1]]
mirror = [sys.executable, "-I", f"{root}/tests/fixtures/mirror_runner.py"]


def mirrored(pair):
    case, serde_hex = pair
    value = bytes.fromhex(case)
    # A wire line holds no raw newline, and the perform around the value adds three levels.
    if not serde_hex or b"\n" in value or value.count(b"[") + value.count(b"{") > 100:
        return None
    line = b'{"perform":{"input":{"value":' + value + b'},"model":false,"word":"mirror"}}\n'
    ran = subprocess.run(mirror, input=line, capture_output=True)
    output = [l for l in ran.stdout.split(b"\n") if l.startswith(b'{"output":')]
    serde = bytes.fromhex(serde_hex)
    found = re.search(rb'"mirrored":(.*),"path":', output[0]) if output else None
    if not found or found.group(1) != serde:
        return f"mirror {value[:60]!r}: {found.group(1)[:80] if found else ran.stdout[:200]!r} != {serde[:80]!r}"
    return ""


with concurrent.futures.ThreadPoolExecutor(8) as pool:
    results = list(pool.map(mirrored, zip(corpus, rounds)))
problems += [r for r in results if r]
say(f"   {sum(1 for r in results if r is not None)} values, each written as serde writes it")

shutil.rmtree(work, ignore_errors=True)
if problems:
    say(f"\ninterop: {len(problems)} problems")
    for problem in problems:
        say(f"- {problem}")
    sys.exit(1)
say("\ninterop: passed")
