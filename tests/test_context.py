"""Errors and refusals as they read, the cancel flag, the host, and a run's context."""

import unittest

from granite.context import CancelFlag, Context, Host, HostModel
from granite.core.emitter import Anchor
from granite.error import (Cancelled, Fault, Refusal, fault_of, host_bad_answer, host_no_answer, host_no_model,
                           kind_named, KINDS)


class Errors(unittest.TestCase):
    def test_a_refusal_reads_as_its_one_reason_or_as_a_count_and_a_list(self):
        one = Refusal("call", "say what to plate")
        self.assertEqual((one.kind, one.said, one.reasons), ("call", "say what to plate", ["say what to plate"]))
        two = Refusal("answer", reasons=["no title", "no body"])
        self.assertEqual(two.said, "2 problems, so nothing was done:\n- no title\n- no body")
        own = Refusal("build", reasons=["no title", "no body"], said="the comic did not build")
        self.assertEqual((str(own), own.reasons[1]), ("the comic did not build", "no body"))
        with self.assertRaises(ValueError):
            Refusal("weather", "rain")
        self.assertEqual(str(Cancelled()), "cancelled")

    def test_a_kind_reads_back_and_one_not_known_is_the_hosts(self):
        for kind in KINDS:
            self.assertEqual(kind_named(kind), kind)
        self.assertEqual(kind_named("weather"), "host")

    def test_anything_else_a_run_raises_is_its_fault_said_as_it_is(self):
        self.assertIsInstance(fault_of(OSError("the disk is full")), Fault)
        self.assertEqual(fault_of(OSError("the disk is full")).said, "the disk is full")
        self.assertEqual(fault_of(KeyError()).said, "KeyError")
        refusal = Refusal("call", "x")
        self.assertIs(fault_of(refusal), refusal)

    def test_the_hosts_model_says_what_went_wrong_as_rust_does(self):
        self.assertEqual(host_no_model("plate").said, "plate asks the host's model, and this host has none loaded")
        self.assertEqual(host_no_answer("the host is gone").said, "the host's model gave no answer: the host is gone")
        self.assertEqual(host_bad_answer("not an object").said,
                         "the host's model answered something that does not decode: not an object")
        self.assertEqual(host_no_model("plate").kind, "model")


class Echo(HostModel):
    def __init__(self):
        self.asks = 0

    def object(self, schema, system, user, max_tokens, seconds, cancel=None):
        self.asks += 1
        return {"system": system, "user": user, "maxTokens": max_tokens, "schema": schema}

    def text(self, system, user, max_tokens, seconds, cancel=None):
        self.asks += 1
        return f"{system} / {user}"


class ContextTests(unittest.TestCase):
    def test_a_cancel_is_set_once_and_wakes_who_listens(self):
        cancel, woken, late = CancelFlag(), [], []
        listener, after = Anchor(), Anchor()
        cancel.on(listener, lambda r, v: woken.append(1))
        self.assertFalse(cancel.is_set)
        cancel.set()
        cancel.set()
        self.assertTrue(cancel.is_set)
        self.assertEqual(woken, [1])
        cancel.on(after, lambda r, v: late.append(1))
        self.assertEqual(late, [1])

    def test_a_context_delivers_notes_and_asks_its_hosts_model(self):
        events, notes = [], []
        model = Echo()
        host = Host("/work", "draw a cat", model, notes.append)
        context = Context("plate-0000abcd", "plate", host, compute=7, sink=events.append)
        self.assertEqual((context.run, context.directory, context.ask, context.compute),
                         ("plate-0000abcd", "/work", "draw a cat", 7))
        context.deliver(1)
        context.deliver(2)
        context.note("round 1")
        self.assertEqual((events, notes), ([1, 2], ["round 1"]))
        self.assertEqual(context.ask_object({"type": "object"}, "be brief", "a cat", 300, 30)["maxTokens"], 300)
        self.assertEqual(context.ask_text("sys", "usr", 10, 5), "sys / usr")
        context.cancel.set()
        with self.assertRaises(Refusal) as refused:
            context.ask_text("sys", "usr")
        self.assertEqual((refused.exception.kind, model.asks), ("model", 2))

    def test_asking_a_host_without_a_model_is_a_model_refusal(self):
        context = Context("r", "plate")
        with self.assertRaises(Refusal) as refused:
            context.ask_text("s", "u")
        self.assertEqual(refused.exception.said, "plate asks the host's model, and this host has none loaded")
        context.deliver(3)  # no sink: let go


if __name__ == "__main__":
    unittest.main()
