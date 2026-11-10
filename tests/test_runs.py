"""The run engine: one writer on main, events drained to the taps before a wait settles, lanes, cancels said at once,
and the run's own error kept. GraniteC's test_engine.c, ported."""

import threading
import time
import unittest
from dataclasses import dataclass

from granite.core import main
from granite.core.emitter import Anchor
from granite.error import Cancelled, Fault, Refusal
from granite.runs import Runs


@dataclass
class Job:
    events: int = 0
    every: float = 0.0
    then: str = "finish"  # finish, refuse, fault, wait-for-cancel


class Engine:
    """A compute and a body that count what they did."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = self.most_at_once = self.computes = 0
        self.fail_compute = False
        self.off_main = True

    def compute(self):
        with self.lock:
            self.computes += 1
        if self.fail_compute:
            raise Fault("no GPU here")
        return self

    def body(self, context, job):
        with self.lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        if main.is_current():
            self.off_main = False
        try:
            for n in range(1, job.events + 1):
                time.sleep(job.every)
                context.deliver({"n": n})
            if job.then == "refuse":
                raise Refusal("call", f"{context.word} needs a title")
            if job.then == "fault":
                raise OSError("the disk is full")
            if job.then == "wait-for-cancel":
                while not context.is_cancelled:
                    time.sleep(0.002)
                context.deliver({"late": True})
        finally:
            with self.lock:
                self.running -= 1

    def runs(self, lanes=2):
        return Runs("plate", self.body, self.compute, lanes)


class Heard:
    def __init__(self, runs):
        self.text, self.count = [], 0
        self.off_main = False
        self.anchor = Anchor()
        runs.tap(self.anchor, Heard.hear_into(self))

    @staticmethod
    def hear_into(heard):
        def hear(receiver, value):
            if not main.is_current():
                heard.off_main = True
            heard.text.append(f"{value.key}:{value.event.get('n', -1)};")
            heard.count += 1
        return hear

    def said(self):
        return "".join(self.text)


class EngineTests(unittest.TestCase):
    def test_a_finished_run_settles_after_its_events_reached_the_taps(self):
        engine = Engine()
        runs = engine.runs()
        heard = Heard(runs)
        runs.perform("plate-1", job=Job(3, 0.001))
        self.assertEqual(heard.said(), "plate-1:1;plate-1:2;plate-1:3;")
        self.assertFalse(heard.off_main)
        self.assertTrue(engine.off_main)
        state = runs.state()
        self.assertEqual((state.runs, state.finished, state.events), (1, 1, 3))

    def test_the_runs_own_refusal_keeps_its_kind_and_a_fault_is_said_as_it_is(self):
        runs = Engine().runs()
        with self.assertRaises(Refusal) as refused:
            runs.perform("a", job=Job(1, 0, "refuse"))
        self.assertEqual((refused.exception.kind, refused.exception.said), ("call", "plate needs a title"))
        with self.assertRaises(Fault) as fault:
            runs.perform("b", job=Job(0, 0, "fault"))
        self.assertEqual(fault.exception.said, "the disk is full")
        self.assertIsInstance(fault.exception.__cause__, OSError)
        state = runs.state()
        self.assertEqual((state.failed, state.last_failure), (2, "the disk is full"))

    def test_a_compute_that_fails_is_the_engines_sentence(self):
        engine = Engine()
        runs = engine.runs()
        engine.fail_compute = True
        with self.assertRaises(Fault) as first:
            runs.perform("a", job=Job(1))
        with self.assertRaises(Fault) as second:
            runs.perform("b", job=Job(1))
        self.assertEqual(first.exception.said, "PackageRun-1 failed: no GPU here")
        self.assertEqual(second.exception.said, "PackageRun-2 failed: no GPU here")
        self.assertEqual(engine.computes, 2)

    def test_lanes_bound_how_many_run_at_once_and_queued_runs_start_in_order(self):
        engine = Engine()
        runs = engine.runs(2)
        results = [None] * 6

        def perform(i):
            try:
                runs.perform(f"call-{i}", job=Job(3, 0.004))
                results[i] = "finished"
            except Exception as error:  # kept for the assertion
                results[i] = error

        threads = [threading.Thread(target=perform, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["finished"] * 6)
        self.assertEqual(engine.most_at_once, 2)
        self.assertEqual(runs.count(), (0, 0))

    def test_a_cancel_settles_at_once_and_drops_what_the_run_says_after(self):
        runs = Engine().runs(1)
        heard = Heard(runs)
        outcomes = {}

        def perform(key, job):
            try:
                runs.perform(key, job=job)
                outcomes[key] = "finished"
            except Exception as error:
                outcomes[key] = error

        first = threading.Thread(target=perform, args=("live", Job(1, 0, "wait-for-cancel")))
        first.start()
        for _ in range(200):
            if heard.count >= 1:
                break
            time.sleep(0.005)
        second = threading.Thread(target=perform, args=("queued", Job(1)))
        second.start()
        for _ in range(200):
            if runs.count()[1] >= 1:
                break
            time.sleep(0.005)
        self.assertEqual(runs.count(), (1, 1))
        runs.cancel("queued")
        second.join()
        self.assertIsInstance(outcomes["queued"], Cancelled)
        runs.cancel("live")
        first.join()
        self.assertIsInstance(outcomes["live"], Cancelled)
        self.assertEqual(outcomes["live"].said, "cancelled")
        time.sleep(0.05)
        main.turn()
        self.assertEqual(heard.said(), "live:1;")  # the late event was dropped
        state = runs.state()
        self.assertEqual((state.cancelled, state.runs), (2, 1))

    def test_the_same_key_asked_again_replaces_the_run_before_it(self):
        runs = Engine().runs(2)
        outcomes = {}

        def perform(name, job):
            try:
                runs.perform("same", job=job)
                outcomes[name] = "finished"
            except Exception as error:
                outcomes[name] = error

        older = threading.Thread(target=perform, args=("older", Job(0, 0, "wait-for-cancel")))
        older.start()
        for _ in range(200):
            if runs.count()[0] >= 1:
                break
            time.sleep(0.005)
        perform("newer", Job(1))
        older.join()
        self.assertIsInstance(outcomes["older"], Cancelled)
        self.assertEqual(outcomes["newer"], "finished")

    def test_a_requested_cancel_set_before_the_start_never_starts_the_run(self):
        from granite.context import CancelFlag
        engine = Engine()
        runs = engine.runs()
        requested = CancelFlag()
        requested.set()
        with self.assertRaises(Cancelled):
            runs.perform("a", requested=requested, job=Job(1))
        self.assertEqual((engine.computes, runs.state().runs, runs.state().cancelled), (0, 0, 1))

    def test_performing_from_main_is_refused_rather_than_waiting_on_itself(self):
        runs = Engine().runs()

        def from_main():
            try:
                runs.perform("x", job=Job())
            except Fault as fault:
                return fault.said
            return "performed"

        self.assertEqual(main.call(from_main), "a run was performed from main, which would wait on itself")


if __name__ == "__main__":
    unittest.main()
