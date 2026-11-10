"""The host's side of a call, against real runners: the parrot (GraniteRust's twin) for every way a call ends, and
the scripted runner for what a real agent never says. GraniteC's test_process.c."""

import os
import signal
import threading
import time
import unittest

from granite import agentjson
from granite.context import CancelFlag, Host, HostModel
from granite.error import host_no_answer
from granite.host import child as child_module, files
from granite.host.process import Process
from granite.host.remote import ENDED, FAULT, REFUSED, UNREADABLE, RemoteError
from tests.support import HERE, runner


class Four(HostModel):
    def object(self, schema, system, user, max_tokens, seconds, cancel=None):
        return {"n": 4}

    def text(self, system, user, max_tokens, seconds, cancel=None):
        raise host_no_answer("text is not asked here")


def said_by(call):
    try:
        output, _ = call()
        return None, output
    except RemoteError as error:
        return error, None


class ProcessTests(unittest.TestCase):
    def test_describe_says_what_it_carries_and_makes_nothing(self):
        entries = Process(runner("parrot_runner")).describe()
        golden = (HERE / "goldens" / "parrot.describe").read_bytes()
        self.assertEqual(agentjson.write({"entries": entries}) + b"\n", golden)

    def test_one_call_is_answered_its_notes_heard_its_ask_answered_and_its_cost_measured(self):
        notes = []
        folder = os.path.realpath(files.temporary_folder("process"))
        host = Host(folder, "count", Four(), notes.append)
        output, cost = Process(runner("parrot_runner")).perform(
            "parrot", '{"text":"hi","notes":2,"askModel":true}', host)
        answer = agentjson.read(output)
        self.assertEqual((answer["said"], answer["modelSaid"], answer["hostDirectory"], answer["ask"]),
                         ("hi", 4, folder, "count"))
        self.assertEqual(notes, ["note 1", "note 2"])
        self.assertEqual((cost.status, cost.asks), ("exit 0", 1))
        self.assertTrue(cost.sentence().startswith("released · peak "))
        self.assertIn(" s CPU in ", cost.sentence())
        self.assertGreater(cost.peak_bytes, 1 << 20)
        files.remove_tree(folder)

    def expect(self, input, kind, sentence, word="parrot", executable=None):
        process = Process(executable or runner("parrot_runner"))
        error, _ = said_by(lambda: process.perform(word, input))
        self.assertIsNotNone(error, input)
        self.assertEqual(error.kind, kind, str(error))
        self.assertTrue(str(error).startswith(sentence), str(error))

    def test_each_way_a_call_ends_is_classified_as_rust_classifies_it(self):
        self.expect('{"text":"x","fail":true}', REFUSED, "parrot refused (call): told to fail")
        self.expect('{"notes":1}', REFUSED, "parrot refused (input): parrot could not read its input: missing field "
                                            "`text`")
        self.expect('{"text":"x","askModel":true}', REFUSED,
                    "parrot refused (model): parrot asks the host's model, and this host has none loaded")
        self.expect('{"text":"x","fault":true}', FAULT, "parrot failed by its own fault: it broke")
        self.expect('{"text":"x","crash":true}', ENDED,
                    "parrot's process ended (signal 6) without answering: this line must not reach the wire")
        self.expect("{}", FAULT, "comic failed by its own fault: no agent is called `comic` in this process; it "
                                 "carries `parrot`", word="comic")
        self.expect("not json", UNREADABLE, "parrot's process broke the wire: the input is not JSON")
        error, _ = said_by(lambda: Process("/nowhere/runner").perform("parrot", "{}"))
        self.assertEqual(str(error), "could not start /nowhere/runner: No such file or directory (os error 2)")
        error, _ = said_by(lambda: Process("no-such-runner-anywhere").perform("parrot", "{}"))
        self.assertEqual(str(error), "could not start no-such-runner-anywhere: No such file or directory (os error 2)")

    def perform_until_cancelled(self, process, input='{"text":"x","delay":30}'):
        cancel, outcome = CancelFlag(), {}

        def perform():
            start = time.monotonic()
            outcome["error"], _ = said_by(lambda: process.perform("parrot", input, None, cancel))
            outcome["took"] = time.monotonic() - start

        thread = threading.Thread(target=perform)
        thread.start()
        time.sleep(0.4)
        cancel.set()
        thread.join(10)
        return outcome["error"], outcome["took"]

    def test_a_cancelled_call_ends_its_process(self):
        error, took = self.perform_until_cancelled(Process(runner("parrot_runner")))
        self.assertIsNotNone(error)
        self.assertLess(took, 5)

    def scripted(self, script):
        return Process(runner("scripted_runner"), {"GRANITE_SCRIPT": script, "PATH": "/usr/bin:/bin"})

    def report(self, directory):
        output, _ = self.scripted("report").perform("x", "{}", Host(directory))
        return agentjson.read(output)

    def test_a_runner_starts_clean_whatever_its_host_blocks_or_ignores(self):
        folder = os.path.realpath(files.temporary_folder("clean"))
        leaked = os.open("/dev/null", os.O_RDONLY)
        os.set_inheritable(leaked, True)  # what a careless host leaves open
        before = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1, signal.SIGTERM})
        kept = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            for through_ctypes in (True, False):
                child_module.use_ctypes = through_ctypes
                with self.subTest(ctypes=through_ctypes):
                    report = self.report(folder)
                    self.assertEqual((report["blocked"], report["ignored"], report["fds"]), ([], [], []))
                    self.assertTrue(report["leads"])
                    self.assertEqual((report["cwd"], report["agent"]), (folder, "1"))
        finally:
            child_module.use_ctypes = True
            signal.pthread_sigmask(signal.SIG_SETMASK, before)
            signal.signal(signal.SIGINT, kept)
            os.close(leaked)
            files.remove_tree(folder)

    def expect_scripted(self, script, kind=None, sentence=None, answer=None):
        error, output = said_by(lambda: self.scripted(script).perform("x", "{}"))
        if answer is not None:
            self.assertIsNone(error, f"{script}: {error}")
            self.assertEqual(output, answer)
        else:
            self.assertIsNotNone(error, script)
            self.assertEqual((error.kind, str(error)), (kind, sentence))

    def test_what_a_real_agent_never_says_is_read_as_rust_reads_it(self):
        self.expect_scripted("major2", UNREADABLE,
                             "x's process broke the wire: it speaks granite-agent/2, and this host granite-agent/1")
        self.expect_scripted("misplaced", answer=b"{}")
        self.expect_scripted("noisy", answer=b'{"heard":true}')
        self.expect_scripted("silent", ENDED, "x's process ended (exit 0) without answering")
        self.expect_scripted("exit-early", ENDED, "x's process ended (exit 7) without answering")
        self.expect_scripted("long-note", answer=b'{"long":true}')

    def test_a_runner_that_ignores_sigterm_is_killed_after_the_grace(self):
        error, took = self.perform_until_cancelled(self.scripted("stubborn"))
        self.assertEqual(str(error), "parrot's process ended (signal 9) without answering")
        self.assertTrue(1.5 < took < 5, took)

    def test_a_grandchild_does_not_outlive_the_call(self):
        output, _ = self.scripted("grandchild").perform("x", "{}")
        grandchild = agentjson.read(output)["grandchild"]
        self.assertGreater(grandchild, 0)
        for _ in range(100):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail(f"the grandchild {grandchild} outlived the call")


if __name__ == "__main__":
    unittest.main()
