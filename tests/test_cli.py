"""The granite command against GraniteRust's: every case in goldens/cli run through this granite and said as
GraniteRust's granite says it, byte for byte once pids, costs and places are made the same (goldens/cli/normalize.py);
and an agent added, listed, run and removed through the command. GraniteC's test_cli.c."""

import importlib.util
import os
import signal
import subprocess
import time
import unittest
from pathlib import Path

from granite import agentjson
from granite.host import files
from tests.support import FIXTURES, runner

ROOT = Path(__file__).parent.parent
GOLDENS = Path(__file__).parent / "goldens" / "cli"
_spec = importlib.util.spec_from_file_location("granite_cli_normalize", GOLDENS / "normalize.py")
normalize_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalize_module)


def run_cli(args, input="", folder=None, environment=None):
    env = {k: v for k, v in os.environ.items() if k != "GRANITE_SHELF"}
    env.update(environment or {})
    done = subprocess.run([runner("granite_cli"), *args], input=input.encode(), capture_output=True, cwd=folder,
                          env=env)
    code = done.returncode if done.returncode >= 0 else 128 - done.returncode
    return code, done.stdout.decode(), done.stderr.decode()


class CLITests(unittest.TestCase):
    def setUp(self):
        self.work = os.path.realpath(files.temporary_folder("cli"))
        self.shelf = os.path.join(self.work, "shelf")
        files.mkdirs(self.shelf)

    def tearDown(self):
        files.remove_tree(self.work)

    def test_every_case_is_said_as_graniterusts_granite_says_it(self):
        cases = [agentjson.read(line) for line in (GOLDENS / "cases.jsonl").read_bytes().splitlines() if line]
        expected = [agentjson.read(line) for line in (GOLDENS / "rust.expected").read_bytes().splitlines() if line]
        self.assertEqual(len(cases), 30)
        places = {"PARROT": runner("parrot_runner"), "SCHEMA": str(ROOT / "Schema"), "SHELF": self.shelf}
        mismatches = []
        for index, (case, want) in enumerate(zip(cases, expected), 1):
            args = [arg for arg in case["args"]]
            for name, path in places.items():
                args = [arg.replace("{" + name + "}", path) for arg in args]
            code, out, err = run_cli(args, case["stdin"], self.work)
            got = (code, normalize_module.normalize(out, places), normalize_module.normalize(err, places))
            if got != (want["code"], want["stdout"], want["stderr"]):
                mismatches.append(f"case {index} {case['args']}:\n  Python {got}\n  Rust   "
                                  f"{(want['code'], want['stdout'], want['stderr'])}")
        self.assertEqual(mismatches, [], "\n".join(mismatches[:4]))

    def test_an_agent_is_added_listed_run_and_removed_through_the_command(self):
        shelf = ["--shelf", self.shelf]
        code, out, err = run_cli(["agents", "add", str(FIXTURES / "agent-repo"), *shelf], folder=self.work)
        self.assertEqual(code, 0, err)
        self.assertEqual(agentjson.read(out.encode())["name"], "parrot-python")
        self.assertIn("installed parrot-python: parrot", err)
        code, out, _ = run_cli(["agents", "list", *shelf])
        self.assertEqual(out, "parrot       parrot-python@" + out.split("@")[1].split(" ")[0] +
                         " · says its text back, in a process of its own.\n")
        executable = os.path.join(self.shelf, "parrot-python", "agent")
        code, out, err = run_cli(["run", executable, "-", "--word", "parrot"], '{"input":{"text":"shelved"}}')
        self.assertEqual(code, 0, err)
        self.assertEqual(agentjson.read(out.encode())["output"]["said"], "shelved")
        code, _, err = run_cli(["agents", "update", "parrot", *shelf])
        self.assertEqual(code, 0, err)
        self.assertTrue(err.rstrip().split("\n")[-1].startswith("updated parrot-python at "), err)
        code, _, err = run_cli(["agents", "rm", "PARROT", *shelf])
        self.assertEqual((code, err), (0, "removed parrot-python\n"))
        code, out, _ = run_cli(["agents", "list", "--json", *shelf])
        self.assertEqual(out, "[]\n")

    def test_an_interrupted_run_ends_its_runner_and_then_itself(self):
        with subprocess.Popen([runner("granite_cli"), "run", runner("parrot_runner"), "-"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            child.stdin.write(b'{"word":"parrot","input":{"text":"x","delay":30}}')
            child.stdin.close()
            time.sleep(1.0)
            started = time.monotonic()
            child.send_signal(signal.SIGINT)
            child.wait(10)
            err = child.stderr.read()
        self.assertEqual(child.returncode, -signal.SIGINT, err)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn(b"released", err)


if __name__ == "__main__":
    unittest.main()
