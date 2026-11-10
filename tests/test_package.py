"""A package call in this process: the input read and checked, the run hosted, the answer checked, stamped and
written; what a host reads when it was not answered; and the entry --describe says. GraniteC's test_package.c."""

import asyncio
import threading
import time
import unittest
from dataclasses import dataclass

import granite
from granite import agentjson
from granite.package import call_id, entry


@dataclass
class EchoInput:
    text: str = granite.field(note="what to say back")
    mode: str | None = None


THE_COMPUTE = object()


class Echo(granite.AgentPackage):
    word = "echo"
    summary = "Says it back."
    module = "Echo"
    type = "Echo.echo"
    guide = "Give it text."
    makes = [granite.Make("card", "a card", ["cards/"])]
    Input = EchoInput

    computes = 0
    fail_next_compute = False
    lock = threading.Lock()

    def compute(self):
        with Echo.lock:
            type(self).computes += 1
            if type(self).fail_next_compute:
                type(self).fail_next_compute = False
                raise granite.Fault("no GPU here")
        return THE_COMPUTE

    def run(self, input, context):
        mode = input.mode or "good"
        if context.compute is not THE_COMPUTE:
            raise granite.Fault("the compute did not reach the run")
        if mode == "refuse":
            raise granite.Refusal("call", "`text` is empty: say something")
        if mode == "fault":
            raise OSError("the disk is full")
        if mode == "none":
            return
        if mode == "array":
            context.deliver([])
            return
        if mode == "slow":
            while not context.is_cancelled:
                time.sleep(0.002)
            return
        if mode == "set":
            context.deliver({"x": {1, 2}})
            return
        answer = {} if mode == "bad" else granite.Answer()
        answer["said"] = input.text
        context.deliver({})  # an early event the answer is not read off
        context.deliver(answer)


class Flaky(Echo):
    word = "flaky"
    summary = "Fails to start once."
    module = "Flaky"
    type = None
    makes = ()


@dataclass
class Said:
    said: str
    model_said: int | None = None
    pid: int = 0


class Async(granite.AgentPackage):
    word = "later"
    summary = "Says it back after a while."
    Input = EchoInput

    async def run(self, input, context):
        if input.mode == "slow":
            await asyncio.sleep(30)
        await asyncio.sleep(0.001)
        context.deliver(granite.Answer(Said(input.text, pid=7)))


class PackageTests(unittest.TestCase):
    def perform(self, package, text, **kwargs):
        return granite.perform(package, text, **kwargs)

    def expect_refusal(self, package, text, kind, said, reason=None):
        with self.assertRaises(granite.Refusal) as refused:
            self.perform(package, text)
        self.assertEqual((refused.exception.kind, refused.exception.said), (kind, said))
        if reason is not None:
            self.assertEqual(refused.exception.reasons[0], reason)

    def test_a_conforming_call_answers_checked_stamped_and_sorted(self):
        self.assertEqual(self.perform(Echo(), '{"text":"hi"}'),
                         b'{"agent":{"word":"echo"},"expected":[],"format":"granite-answer/1","made":[],'
                         b'"path":null,"paths":[],"payloads":[],"problems":[],"said":"hi"}')

    def test_input_that_does_not_read_is_an_input_refusal_in_rusts_sentence(self):
        echo = Echo()
        self.expect_refusal(echo, "not json", "input", "echo could not read its input: expected ident at line 1 column 2",
                            "expected ident at line 1 column 2")
        self.expect_refusal(echo, '{"mode":"good"}', "input", "echo could not read its input: missing field `text`",
                            "missing field `text`")
        self.expect_refusal(echo, '{"text":5}', "input",
                            "echo could not read its input: invalid type: integer `5`, expected a string")

    def test_what_the_run_raises_is_what_the_host_reads(self):
        echo = Echo()
        self.expect_refusal(echo, '{"text":"","mode":"refuse"}', "call", "`text` is empty: say something",
                            "`text` is empty: say something")
        with self.assertRaises(granite.Fault) as fault:
            self.perform(echo, '{"text":"x","mode":"fault"}')
        self.assertEqual(fault.exception.said, "the disk is full")
        with self.assertRaises(granite.Fault) as fault:
            self.perform(echo, '{"text":"x","mode":"set"}')
        self.assertIn("a set is not JSON", fault.exception.said)

    def test_an_answer_that_is_missing_or_breaks_the_standard_is_an_answer_refusal(self):
        echo = Echo()
        self.expect_refusal(echo, '{"text":"x","mode":"none"}', "answer", "echo finished without an answer")
        self.expect_refusal(echo, '{"text":"x","mode":"array"}', "answer",
                            "echo answered something that is not an object")
        self.expect_refusal(echo, '{"text":"x","mode":"bad"}', "answer",
                            "echo's answer breaks granite-answer/1: `paths` is missing: an answer names every file it "
                            "wrote, `[]` when none",
                            "`paths` is missing: an answer names every file it wrote, `[]` when none")

    def test_compute_is_made_once_and_tried_again_after_it_fails(self):
        flaky = Flaky()
        Flaky.fail_next_compute = True
        before = Flaky.computes
        with self.assertRaises(granite.Fault) as failed:
            self.perform(flaky, '{"text":"a"}')
        self.assertRegex(failed.exception.said, r"^PackageRun-\d+ failed: no GPU here$")
        for _ in range(3):
            self.perform(flaky, '{"text":"a"}')
        self.assertEqual(Flaky.computes - before, 2)

    def test_a_call_is_cancelled_by_its_flag_before_or_while_it_runs(self):
        for package in (Echo(), Async()):
            with self.subTest(package.word):
                cancel, outcome = granite.CancelFlag(), []

                def perform():
                    try:
                        self.perform(package, '{"text":"x","mode":"slow"}', cancel=cancel)
                        outcome.append("answered")
                    except granite.GraniteError as error:
                        outcome.append(error)

                thread = threading.Thread(target=perform)
                thread.start()
                time.sleep(0.05)
                cancel.set()
                thread.join(5)
                self.assertIsInstance(outcome[0], granite.Cancelled)
                early = granite.CancelFlag()
                early.set()
                with self.assertRaises(granite.Cancelled):
                    self.perform(package, '{"text":"x","mode":"slow"}', cancel=early)

    def test_an_async_run_answers_with_a_dataclass_flattened(self):
        self.assertEqual(granite.perform_value(Async(), {"text": "hi"}),
                         {"agent": {"word": "later"}, "expected": [], "format": "granite-answer/1", "made": [],
                          "modelSaid": None, "path": None, "paths": [], "payloads": [], "pid": 7, "problems": [],
                          "said": "hi"})

    def test_an_entry_says_what_rust_says_and_leaves_out_what_is_empty(self):
        self.assertEqual(agentjson.write(entry(Echo())),
                         b'{"fields":{"text":"what to say back"},"guide":"Give it text.","kind":"package",'
                         b'"lanes":2,"makes":[{"into":["cards/"],"kind":"card","what":"a card"}],'
                         b'"module":"Echo","schema":{"object":[{"name":"text","required":true,"schema":"string"},'
                         b'{"name":"mode","required":false,"schema":"string"}]},"summary":"Says it back.",'
                         b'"type":"Echo.echo","word":"echo"}')

        class Bare(granite.AgentPackage):
            word, summary, module, type, lanes = "bare", "s", "M", "M.Bare", granite.UNBOUNDED

        self.assertEqual(agentjson.write(entry(Bare())),
                         b'{"fields":{},"kind":"package","module":"M","schema":"any","summary":"s",'
                         b'"type":"M.Bare","word":"bare"}')
        self.assertEqual(entry(Async())["type"], "tests.Async")
        self.assertEqual(entry(Async())["module"], "tests")

    def test_a_roster_keeps_the_first_package_of_a_word_and_refuses_a_bad_word(self):
        class EchoAgain(granite.AgentPackage):
            word, summary, module = "echo", "a second echo", "Other"

        roster = granite.Roster(Echo, Flaky, EchoAgain)
        self.assertEqual(len(roster.entries()), 2)
        self.assertIs(roster.find("echo"), roster.packages[0])
        self.assertIsNone(roster.find("nobody"))

        class Loud(granite.AgentPackage):
            word, summary = "Loud", "s"

        with self.assertRaises(ValueError) as said:
            granite.Roster(Loud)
        self.assertIn("one lowercase word", str(said.exception))

    def test_call_ids_are_the_word_and_eight_hex(self):
        ids = {call_id("echo") for _ in range(100)}
        self.assertEqual(len(ids), 100)
        for one in ids:
            self.assertRegex(one, r"^echo-[0-9a-f]{8}$")


if __name__ == "__main__":
    unittest.main()
