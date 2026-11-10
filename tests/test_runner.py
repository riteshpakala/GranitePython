"""The parrot's runner as a process: its --describe is the Rust parrot's bytes, and a call over its stdin and stdout
ends with Rust's lines and exit codes. GraniteC's test_runner.c. Test plumbing only: the host's own process layer is
granite.host."""

import os
import re
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).parent
PARROT = HERE / "fixtures" / "parrot_runner.py"
MIRROR = HERE / "fixtures" / "mirror_runner.py"


def run(runner, argument=None, input=b"", term_after=0.0):
    command = [sys.executable, "-I", str(runner)] + ([argument] if argument else [])
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        if term_after > 0:
            child.stdin.write(input)
            child.stdin.flush()
            time.sleep(term_after)
            child.send_signal(signal.SIGTERM)
            out, err = child.communicate()
        else:
            out, err = child.communicate(input)
    return child.returncode, out.decode(), err.decode()


def pid_free(text):
    return re.sub(r'"pid":\d+', '"pid":1', text)


class RunnerTests(unittest.TestCase):
    def test_describe_is_the_rust_parrots_bytes(self):
        code, out, _ = run(PARROT, "--describe")
        self.assertEqual(code, 0)
        self.assertEqual(out.encode(), (HERE / "goldens" / "parrot.describe").read_bytes())

    def test_a_call_answers_on_the_wire_and_nothing_else_reaches_it(self):
        code, out, err = run(PARROT, input=b'{"perform":{"ask":"say","directory":"/p","input":{"notes":1,"text":"hi"},'
                                           b'"model":false,"word":"parrot"}}\n')
        self.assertEqual(code, 0)
        self.assertEqual(pid_free(out),
                         '{"hello":{"answer":"granite-answer/1","pid":1,"wire":"granite-agent/1"}}\n'
                         '{"note":"note 1"}\n'
                         '{"output":{"agent":{"word":"parrot"},"ask":"say","directory":null,"expected":[],'
                         '"format":"granite-answer/1","hostDirectory":"/p","made":[],"modelSaid":null,"path":null,'
                         '"paths":[],"payloads":[],"pid":1,"problems":[],"said":"hi"}}\n')
        self.assertIn("this line must not reach the wire", err)

    def test_each_ending_is_rusts_line_and_exit(self):
        rows = [
            ('{"perform":{"input":{"fail":true,"text":"x"},"model":false,"word":"parrot"}}\n', 3,
             '{"failed":"told to fail","refused":{"kind":"call","reasons":["told to fail: leave out `fail`"]}}\n'),
            ('{"perform":{"input":{"fault":true,"text":"x"},"model":false,"word":"parrot"}}\n', 1,
             '{"failed":"it broke"}\n'),
            ('{"perform":{"input":{"text":"x","askModel":true},"model":false,"word":"parrot"}}\n', 3,
             '{"failed":"parrot asks the host\'s model, and this host has none loaded","refused":{"kind":"model",'
             '"reasons":["parrot asks the host\'s model, and this host has none loaded"]}}\n'),
            ('{"perform":{"input":{},"model":false,"word":"comic"}}\n', 1,
             '{"failed":"no agent is called `comic` in this process; it carries `parrot`"}\n'),
            ("", 2, '{"failed":"the host left before making a call"}\n'),
            ('{"cancel":{}}\n', 130, '{"failed":"cancelled before the call began"}\n'),
            ('{"perform":{"input":{"text":"x","notes":-1},"model":false,"word":"parrot"}}\n', 3,
             '{"failed":"parrot could not read its input: invalid value: integer `-1`, expected u32","refused":'
             '{"kind":"input","reasons":["invalid value: integer `-1`, expected u32"]}}\n'),
        ]
        for text, code, last in rows:
            with self.subTest(text):
                got, out, _ = run(PARROT, input=text.encode())
                self.assertEqual(got, code)
                self.assertEqual(out.splitlines(keepends=True)[-1], last)

    def test_a_crash_ends_with_its_signal_and_sigterm_cancels(self):
        code, _, _ = run(PARROT, input=b'{"perform":{"input":{"crash":true,"text":"x"},"model":false,"word":"parrot"}}\n')
        self.assertEqual(code, -signal.SIGABRT)
        code, out, _ = run(PARROT, input=b'{"perform":{"input":{"delay":5,"text":"x"},"model":false,"word":"parrot"}}\n',
                           term_after=0.5)
        self.assertEqual(code, 130)
        self.assertIn('{"note":"waiting 5 s as pid ', out)
        self.assertTrue(out.endswith('{"failed":"cancelled"}\n'), out)

    def test_the_wire_is_kept_from_the_runners_children(self):
        code, out, _ = run(MIRROR, input=b'{"perform":{"input":{"value":[1,2.5,"\\u00e9",null]},"model":false,'
                                          b'"word":"mirror"}}\n')
        self.assertEqual(code, 0)
        self.assertIn('"mirrored":[1,2.5,"é",null]', out)


if __name__ == "__main__":
    unittest.main()
