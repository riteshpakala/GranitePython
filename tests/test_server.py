"""One call a server answers, ported from GraniteRust's server tests by way of GraniteC's test_server.c: a hello
first, one output or failed last, the host's model asked across the channel, and each way a host can end a call with
the exit it ends with. The runner serves on a thread; the test is its host."""

import threading
import time
import unittest
from dataclasses import dataclass

import granite
from granite import channel, server, wire


@dataclass
class EchoInput:
    text: str
    delay: float | None = None


class Echo(granite.AgentPackage):
    word, summary, module = "echo", "Says it back.", "Common"
    Input = EchoInput

    def run(self, input, context):
        until = time.monotonic() + (input.delay or 0)
        while time.monotonic() < until and not context.is_cancelled:
            time.sleep(0.002)
        context.deliver(granite.Answer(said=input.text))


@dataclass
class HostedInput:
    ask_model: bool | None = granite.field(name="ask_model", default=None)


class Hosted(granite.AgentPackage):
    word, summary, module = "hosted", "Asks its host.", "Common"
    Input = HostedInput

    def run(self, input, context):
        context.note("offered")
        answer = granite.Answer()
        if input.ask_model:
            answer["n"] = context.ask_object({"type": "object"}, "system", "user", 3000, 300)["n"]
        answer["directory"] = context.directory
        context.deliver(answer)


ROSTER = granite.Roster(Echo, Hosted)


def perform(word, input, model=False):
    return wire.Perform(word, input, directory="/p", model=model)


class Transcript:
    def __init__(self):
        self.exit = None
        self.said = []

    @property
    def last(self):
        return self.said[-1]

    @property
    def failed_said(self):
        return self.last.error.said if isinstance(self.last, wire.Failed) else "(not failed)"


def call(sent, close_after=False, answers=()):
    """One call: ``sent`` said to the runner in order, ``answers`` given to its asks of the model in order."""
    host, runner = channel.pair()
    transcript = Transcript()
    serving = threading.Thread(target=lambda: setattr(transcript, "exit", server.serve_channel(runner, ROSTER)))
    serving.start()
    transcript.said.append(host.recv())  # the hello
    for message in sent:
        host.send(message)
    if close_after:
        host.close()
    answers = list(answers)
    while (message := host.recv()) is not None:
        transcript.said.append(message)
        if isinstance(message, wire.ModelAsk) and answers:
            host.send(wire.ModelAnswer(message.id, object=answers.pop(0)))
        if isinstance(message, (wire.Output, wire.Failed)):
            break
    serving.join(10)
    host.close()
    runner.close()
    return transcript


class ServerTests(unittest.TestCase):
    def test_a_call_is_answered_once_after_a_hello_that_names_the_answer_standard(self):
        t = call([perform("echo", {"text": "hi"})])
        self.assertEqual(t.exit, server.ANSWERED)
        self.assertIsInstance(t.said[0], wire.Hello)
        self.assertEqual((t.said[0].wire, t.said[0].answer), ("granite-agent/1", "granite-answer/1"))
        self.assertIsInstance(t.last, wire.Output)
        self.assertEqual((t.last.value["said"], t.last.value["format"]), ("hi", "granite-answer/1"))

    def test_the_model_is_asked_across_the_channel(self):
        t = call([perform("hosted", {"ask_model": True}, model=True)], answers=[{"n": 4}])
        self.assertEqual(t.exit, server.ANSWERED)
        self.assertTrue(any(isinstance(m, wire.Note) and m.text == "offered" for m in t.said))
        self.assertTrue(any(isinstance(m, wire.ModelAsk) and m.id == 1 and m.max_tokens == 3000 for m in t.said))
        self.assertEqual((t.last.value["n"], t.last.value["directory"]), (4, "/p"))

    def test_a_call_that_needs_a_model_the_host_does_not_offer_is_refused(self):
        t = call([perform("hosted", {"ask_model": True})])
        self.assertEqual(t.exit, server.REFUSED)
        self.assertEqual(t.last.error.kind, "model")
        self.assertEqual(t.failed_said, "hosted asks the host's model, and this host has none loaded")

    def test_a_host_that_leaves_with_an_ask_open_ends_it_without_an_answer(self):
        t = call([perform("hosted", {"ask_model": True}, model=True)], close_after=True)
        self.assertEqual(t.exit, server.REFUSED)
        self.assertEqual(t.failed_said, "the host's model gave no answer: the host is gone")

    def test_input_that_does_not_read_is_refused_as_input_with_exit_three(self):
        t = call([perform("echo", {"times": 1})])
        self.assertEqual(t.exit, server.REFUSED)
        self.assertEqual(t.last.error.kind, "input")
        self.assertEqual(t.failed_said, "echo could not read its input: missing field `text`")

    def test_an_unknown_word_fails_and_says_what_the_process_carries(self):
        t = call([perform("parrot", {})])
        self.assertEqual(t.exit, server.FAILED)
        self.assertEqual(t.failed_said, "no agent is called `parrot` in this process; it carries `echo`, `hosted`")
        self.assertIsInstance(t.last.error, granite.Fault)

    def test_a_cancel_ends_the_call_with_one_thirty(self):
        t = call([perform("echo", {"text": "slow", "delay": 5}), wire.Cancel()])
        self.assertEqual(t.exit, server.CANCELLED)
        self.assertEqual(t.failed_said, "cancelled")
        t = call([wire.Cancel()])
        self.assertEqual(t.exit, server.CANCELLED)
        self.assertEqual(t.failed_said, "cancelled before the call began")

    def test_a_cancel_wakes_an_ask_of_the_model(self):
        host, runner = channel.pair()
        done = {}
        serving = threading.Thread(target=lambda: done.setdefault("exit", server.serve_channel(runner, ROSTER)))
        serving.start()
        host.recv()
        host.send(perform("hosted", {"ask_model": True}, model=True))
        said = []
        while not isinstance(message := host.recv(), wire.ModelAsk):
            said.append(message)
        host.send(wire.Cancel())
        last = host.recv()
        serving.join(10)
        self.assertEqual(done["exit"], server.CANCELLED)
        self.assertEqual(last.error.said, "cancelled")  # said at once; what the run says after is dropped
        # The run was blocked in its ask; the cancel woke it, so its thread ends.
        for _ in range(200):
            if not any(t.name == "granite.run" for t in threading.enumerate()):
                break
            time.sleep(0.01)
        self.assertFalse(any(t.name == "granite.run" for t in threading.enumerate()))
        host.close()
        runner.close()

    def test_a_host_that_speaks_out_of_turn_or_leaves_ends_the_process_with_two(self):
        t = call([wire.Note("hi")])
        self.assertEqual(t.exit, server.UNREADABLE)
        self.assertEqual(t.failed_said, "the host said `note`, which is not a host's message")
        t = call([], close_after=True)
        self.assertEqual(t.exit, server.UNREADABLE)
        self.assertEqual(t.failed_said, "the host left before making a call")
        t = call([perform("echo", {"text": "slow", "delay": 5}), perform("echo", {"text": "again"})])
        self.assertEqual(t.exit, server.UNREADABLE)
        self.assertEqual(t.failed_said, "a process answers one call; the host sent a second `perform`")


if __name__ == "__main__":
    unittest.main()
