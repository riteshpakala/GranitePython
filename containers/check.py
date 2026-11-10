#!/usr/bin/env python3
"""Installs a Python agent repository and checks what its runner says against the standard, in an Apple `container`
(Linux, arm64) or, with --native, on this machine. GraniteRust's containers/check.sh, for Python.

  containers/check.sh <agent folder | git URL[@revision]> [--native] [--granite <GranitePython clone>]
                      [--out <dir>] [--keep]
  (GRANITE_CHECK_MEMORY, 4g, and GRANITE_CHECK_CPUS, 4, size each container)

1. Install it with GranitePython's installer: pyproject.toml → zipapp → --describe → installation.json, the record
   checked against granite-agent-installation.schema.json.
2. Describe it: every entry checked against granite-agent-entry.schema.json.
3. Every call the agent keeps as a fixture in its Schema/examples/, in name order, in one folder (the call's folder,
   so a fixture names its files relative to it, and one call may build on the last):
   *.perform.json  {"perform": {…}, "expect": {"exit": 0}}: answered; the answer checked against Granite's
                   granite-agent-answer.schema.json and Craft's craft-agent-output.schema.json; every path written;
                   `made` of kinds it declares; every `expected` made.
   *.refusal.json  {"perform": {…}, "expect": {"exit": 3, "kind": "call"}}: refused with that kind.

GranitePython itself comes from this checkout, or from --granite <clone>. --native builds its granite command into a
scratch folder; the container builds it into the image. --out keeps each answer, the shelf and the call's folder.
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

here = os.path.dirname(os.path.abspath(__file__))


def failed(said: str) -> None:
    print(f"FAILED: {said}", file=sys.stderr)
    sys.exit(1)


def say(said: str) -> None:
    print(f"\n== {said}", flush=True)


def main(argv: list[str]) -> int:
    agent, mode, granite, out, keep = "", "container", os.path.dirname(here), "", False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--native":
            mode = "native"
        elif a == "--granite":
            granite, i = os.path.abspath(argv[i + 1]), i + 1
        elif a == "--out":
            out, i = argv[i + 1], i + 1
        elif a == "--keep":
            keep = True
        elif a.startswith("-"):
            print(f"unknown option {a}", file=sys.stderr)
            return 2
        else:
            agent = a
        i += 1
    if not agent:
        print(__doc__, file=sys.stderr)
        return 2

    scratch = os.path.realpath(tempfile.mkdtemp(prefix="granitepython-check."))
    try:
        return check(agent, mode, granite, out, scratch)
    finally:
        if not keep and not out:
            shutil.rmtree(scratch, ignore_errors=True)


def git(*arguments: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *arguments], cwd=cwd, capture_output=True, text=True)


def check(agent: str, mode: str, granite: str, out: str, scratch: str) -> int:
    shelf, work = os.path.join(scratch, "shelf"), os.path.join(scratch, "work")
    os.makedirs(shelf)
    os.makedirs(work)

    # The agent, as an installer would fetch it: a clone of its commit when it is a repository of its own, else a
    # copy of the folder (one inside another repository, as GranitePython's own fixture is).
    if os.path.isdir(agent):
        agent_dir = os.path.realpath(agent)
        top = git("rev-parse", "--show-toplevel", cwd=agent_dir).stdout.strip()
        if top and os.path.realpath(top) == agent_dir and git("rev-parse", "HEAD", cwd=agent_dir).returncode == 0:
            if git("status", "--porcelain", cwd=agent_dir).stdout.strip():
                print(f"note: {agent_dir} has uncommitted changes; the check installs its last commit", file=sys.stderr)
            subprocess.run(["git", "clone", "--quiet", agent_dir, os.path.join(scratch, "agent")], check=True)
        else:
            shutil.copytree(agent_dir, os.path.join(scratch, "agent"),
                            ignore=shutil.ignore_patterns(".git", "build", "__pycache__"))
        examples = os.path.join(agent_dir, "Schema", "examples")
        source_container, source_native = "/scratch/agent", os.path.join(scratch, "agent")
    else:
        url = agent.rsplit("@", 1)[0] if "@" in agent.rsplit("/", 1)[-1] else agent
        read = os.path.join(scratch, "agent-read")
        if subprocess.run(["git", "clone", "--quiet", url, read], capture_output=True).returncode != 0:
            failed(f"could not clone {url}")
        if "@" in agent.rsplit("/", 1)[-1]:
            git("checkout", "--quiet", agent.rsplit("@", 1)[1], cwd=read)
        examples = os.path.join(read, "Schema", "examples")
        source_container = source_native = agent
    if os.path.isdir(examples):
        for name in os.listdir(examples):
            path = os.path.join(examples, name)
            (shutil.copytree if os.path.isdir(path) else shutil.copy2)(path, os.path.join(work, name))
    commit = git("rev-parse", "--short=12", "HEAD", cwd=granite).stdout.strip() or "unknown"

    if mode == "container":
        container = shutil.which("container") or next(iter(sorted(glob.glob(os.path.expanduser(
            "~/.local/apple-container/*/bin/container")), reverse=True)), None)
        if not container:
            failed("Apple's container is not installed: https://github.com/apple/container/releases")
        if subprocess.run([container, "system", "status"], capture_output=True).returncode != 0:
            subprocess.run([container, "system", "start"], check=True)
        say("building the check image")
        subprocess.run([container, "build", "--tag", "granitepython:check", "--build-arg", f"GRANITE_COMMIT={commit}",
                        "--file", os.path.join(granite, "containers", "Dockerfile"), granite], check=True)
        sizing = ["--memory", os.environ.get("GRANITE_CHECK_MEMORY", "4g"),
                  "--cpus", os.environ.get("GRANITE_CHECK_CPUS", "4")]
        command = [container, "run", "--rm", *sizing, "--volume", f"{scratch}:/scratch", "granitepython:check"]

        def inside(path: str) -> str:
            return "/scratch" + path[len(scratch):]

        source = source_container
        schemas = ["--schemas", "/usr/local/share/granitepython/schemas"]
    else:
        say(f"building GranitePython's granite from {granite}")
        cli = os.path.join(scratch, "granite")
        subprocess.run([sys.executable, os.path.join(granite, "scripts", "build-cli.py"), "--out", cli,
                        "--python", sys.executable], check=True, env=dict(os.environ, GRANITE_COMMIT=commit),
                       stdout=subprocess.DEVNULL)
        command = [cli]

        def inside(path: str) -> str:
            return path

        source = source_native
        schemas = ["--schemas", os.path.join(granite, "Schema"), "--schemas",
                   os.path.join(granite, "containers", "schemas")]

    def granite_cmd(*arguments: str, output: str | None = None) -> int:
        with open(output, "wb") if output else open(os.devnull, "wb") as sink:
            done = subprocess.run([*command, *arguments], stdout=sink if output else None)
        return done.returncode

    def granite_check(schema: str, path: str) -> None:
        if granite_cmd("check", *schemas, "--schema", schema, inside(path)) != 0:
            failed(f"{os.path.basename(path)} does not keep {schema}")

    say(f"1. installing {agent} ({mode})")
    record_path = os.path.join(scratch, "installation.json")
    if granite_cmd("agents", "add", source, "--shelf", inside(shelf), output=record_path) != 0:
        failed(f"{agent} did not install")
    record = json.load(open(record_path))
    name = record["name"]
    runner = inside(os.path.join(shelf, name, "agent"))
    granite_check("granite-agent-installation.schema.json", os.path.join(shelf, name, "installation.json"))
    print(f"installed {name}: {', '.join(e['word'] for e in record['entries'])} · granite {record.get('granite')}")

    say("2. describing it")
    entries_path = os.path.join(scratch, "entries.json")
    if granite_cmd("describe", runner, output=entries_path) != 0:
        failed("describe failed")
    entries = json.load(open(entries_path))
    for index, entry in enumerate(entries):
        path = os.path.join(scratch, f"entry-{index}.json")
        with open(path, "w") as written:
            json.dump(entry, written)
        granite_check("granite-agent-entry.schema.json", path)
    makes = [m["kind"] for e in entries for m in e.get("makes", [])]

    say("3. calling it")
    calls = 0
    fixtures = sorted(glob.glob(os.path.join(work, "*.perform.json")) + glob.glob(os.path.join(work, "*.refusal.json")))
    for fixture in fixtures:
        calls += 1
        label = os.path.basename(fixture)[: -len(".json")]
        expect = json.load(open(fixture)).get("expect", {})
        want_exit = expect.get("exit", 0)
        line_path = os.path.join(scratch, f"{label}.line")
        code = granite_cmd("run", runner, inside(fixture), "--directory", inside(work), output=line_path)
        line_text = open(line_path).read()
        if code != want_exit:
            failed(f"{label}: exit {code}, expected {want_exit}: {line_text}")
        line = json.loads(line_text)
        if want_exit == 0:
            answer = line["output"]
            answer_path = os.path.join(scratch, f"{label}.answer.json")
            with open(answer_path, "w") as written:
                json.dump(answer, written)
            granite_check("granite-agent-answer.schema.json", answer_path)
            granite_check("craft-agent-output.schema.json", answer_path)
            if answer.get("format") != "granite-answer/1" or type(answer.get("agent", {}).get("word")) is not str:
                failed(f"{label}: not stamped")
            for path in answer.get("paths", []):
                # A relative path is the call's folder's; an absolute one in a container is under /scratch.
                if not path.startswith("/"):
                    path = os.path.join(work, path)
                elif mode == "container" and path.startswith("/scratch"):
                    path = scratch + path[len("/scratch"):]
                if not os.path.exists(path):
                    failed(f"{label}: {path} is named in paths and is not there")
            if any(m["kind"] not in makes for m in answer.get("made", [])):
                failed(f"{label}: made a kind it does not declare")
            if set(e["name"] for e in answer.get("expected", [])) - set(m["name"] for m in answer.get("made", [])):
                failed(f"{label}: expected something it did not make")
            made = ", ".join(f"{m['kind']} {m['name']} ({m.get('state', 'written')})" for m in answer.get("made", []))
            print(f"{label}: answered{', ' + made if made else ''}")
        else:
            kind = (line.get("refused") or {}).get("kind", "none")
            if kind != expect.get("kind"):
                failed(f"{label}: refused as {kind}, expected {expect.get('kind')}: {line_text}")
            print(f"{label}: refused ({kind}): {line['failed']}")
    if calls == 0:
        print(f"note: {agent} keeps no Schema/examples/*.perform.json or *.refusal.json; nothing was called",
              file=sys.stderr)
    if out:
        os.makedirs(out, exist_ok=True)
        shutil.copytree(scratch, out, dirs_exist_ok=True)
        print(f"kept in {out}")
    say(f"check passed: {name}, {calls} calls ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
