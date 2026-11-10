"""The kernel: Granite's EmitterTests ported (receivers held weakly and ended with their owners, keys that replace and
tell who they displaced, ordered re-entrant delivery, nothing released under the lock, concurrent use), the lock rule,
and granite.main's order."""

import gc
import threading
import unittest

from granite.core import locked, main
from granite.core.emitter import Anchor, Displaceable, Emitter, EmitterError
from granite.core.locked import Locked, LockRuleError


class Probe:
    """A receiver: only its identity matters."""


class DisplacedProbe(Displaceable):
    def __init__(self):
        self.count = 0

    def displaced(self):
        self.count += 1


class OnRelease:
    """Runs ``body`` as it is let go: a handler's capture that acts as it is released."""

    def __init__(self, body):
        self.body = body

    def __del__(self):
        self.body()


def holding(capture):
    """A handler that keeps ``capture`` alive for as long as the subscription is."""
    return lambda receiver, value: capture


class Strict(unittest.TestCase):
    """Every test runs with the lock rule strict: the kernel asking for itself under a lock fails the test."""

    def setUp(self):
        locked.strict = True
        locked.violations.clear()

    def tearDown(self):
        locked.strict = False
        self.assertEqual(locked.violations, [], "the kernel asked for itself under a lock")


class EmitterTests(Strict):
    def test_receivers_run_in_subscription_order(self):
        emitter, log = Emitter(), []
        first, second = Probe(), Probe()
        emitter.on(first, lambda r, v: log.append(f"first {v}"))
        emitter.on(second, lambda r, v: log.append(f"second {v}"))
        emitter.send(1)
        self.assertEqual(log, ["first 1", "second 1"])

    def test_the_handler_is_handed_its_receiver(self):
        emitter, seen = Emitter(), []
        receiver = Probe()
        emitter.on(receiver, lambda handed, v: seen.append(handed))
        emitter.send(1)
        self.assertIs(seen[0], receiver)

    def test_releasing_the_receiver_ends_the_subscription(self):
        emitter, log = Emitter(), []
        receiver = Probe()
        emitter.on(receiver, lambda r, v: log.append(v))
        emitter.send(1)
        self.assertEqual(emitter.receiver_count, 1)
        del receiver
        self.assertEqual(emitter.receiver_count, 0)
        emitter.send(2)
        self.assertEqual(log, [1])

    def test_a_key_replaces_its_subscription_and_moves_it_last(self):
        emitter, log = Emitter(), []
        first, middle, last = Probe(), Probe(), Probe()
        emitter.on(first, lambda r, v: log.append("first"), key="slot")
        emitter.on(middle, lambda r, v: log.append("middle"))
        emitter.on(last, lambda r, v: log.append("last"), key="slot")
        emitter.send(1)
        self.assertEqual(log, ["middle", "last"])
        self.assertEqual(emitter.receiver_count, 2)

    def test_replacing_a_key_tells_the_displaced_receiver(self):
        emitter = Emitter()
        first, second = DisplacedProbe(), DisplacedProbe()
        emitter.on(first, lambda r, v: None, key="slot")
        emitter.on(second, lambda r, v: None, key="slot")
        self.assertEqual((first.count, second.count), (1, 0))
        emitter.on(second, lambda r, v: None, key="slot")
        self.assertEqual(second.count, 0, "replacing its own subscription is not a displacement")

    def test_removing_all_tells_every_receiver(self):
        emitter = Emitter()
        first, second = DisplacedProbe(), DisplacedProbe()
        emitter.on(first, lambda r, v: None)
        emitter.on(second, lambda r, v: None)
        emitter.remove_all()
        self.assertEqual(emitter.receiver_count, 0)
        self.assertEqual((first.count, second.count), (1, 1))

    def test_off_matches_the_receiver_and_the_key(self):
        emitter, log = Emitter(), []
        receiver, other = Probe(), Probe()
        emitter.on(receiver, lambda r, v: log.append("a"), key="a")
        emitter.on(receiver, lambda r, v: log.append("b"), key="b")
        emitter.on(other, lambda r, v: log.append("c"), key="c")
        emitter.off(other, key="a")
        emitter.off(receiver, key="a")
        emitter.send(1)
        self.assertEqual(log, ["b", "c"])
        emitter.off(receiver)
        emitter.send(2)
        self.assertEqual(log, ["b", "c", "c"])

    def test_a_receiver_released_earlier_in_the_same_send_is_not_reached(self):
        emitter, log = Emitter(), []
        first = Probe()
        held = [Probe()]

        def let_go(receiver, value):
            log.append("first")
            held.clear()

        emitter.on(first, let_go)
        emitter.on(held[0], lambda r, v: log.append("second"))
        emitter.send(1)
        self.assertEqual(log, ["first"])

    def test_off_during_a_send_takes_effect_from_the_next_send(self):
        emitter, log = Emitter(), []
        first, second = Probe(), Probe()
        emitter.on(first, lambda r, v: (log.append(f"first {v}"), emitter.off(second)))
        emitter.on(second, lambda r, v: log.append(f"second {v}"))
        emitter.send(1)
        emitter.send(2)
        self.assertEqual(log, ["first 1", "second 1", "first 2"])

    def test_handlers_may_send_and_subscribe_reentrantly(self):
        emitter, log = Emitter(), []
        receiver, late = Probe(), Probe()

        def handler(r, value):
            log.append(value)
            if value == 1:
                emitter.on(late, lambda r, v: log.append(v * 10))
                emitter.send(2)

        emitter.on(receiver, handler)
        emitter.send(1)
        self.assertEqual(log, [1, 2, 20])

    def test_a_value_less_emitter_sends(self):
        emitter, log = Emitter(), []
        receiver = Anchor()
        emitter.on(receiver, lambda r, v: log.append(v))
        emitter.send()
        self.assertEqual(log, [None])

    def test_a_receiver_that_cannot_be_held_weakly_or_a_handler_that_holds_it_is_refused(self):
        emitter = Emitter()
        with self.assertRaisesRegex(EmitterError, "cannot be held weakly"):
            emitter.on(7, lambda r, v: None)
        receiver = Probe()
        with self.assertRaisesRegex(EmitterError, "holds its own receiver"):
            emitter.on(receiver, lambda r, v: receiver)

    # Nothing is released under the lock: a handler's capture that sends, subscribes or clears as it is let go
    # finds the emitter's lock free (strict mode would record it otherwise).

    def test_a_handler_released_by_a_replaced_key_may_send_to_its_emitter(self):
        emitter, log = Emitter(), []
        first, second = Probe(), Probe()
        emitter.on(first, holding(OnRelease(lambda: emitter.send(99))), key="slot")
        emitter.on(second, lambda r, v: log.append(v), key="slot")
        self.assertEqual(log, [99], "the send made during the release reached the new subscription")

    def test_a_handler_released_by_off_may_send_to_its_emitter(self):
        emitter, log = Emitter(), []
        leaving, staying = Probe(), Probe()
        emitter.on(staying, lambda r, v: log.append(v))
        emitter.on(leaving, holding(OnRelease(lambda: emitter.send(7))))
        emitter.off(leaving)
        self.assertEqual(log, [7])

    def test_a_handler_released_by_remove_all_may_send_to_its_emitter(self):
        emitter = Emitter()
        receiver = Probe()
        emitter.on(receiver, holding(OnRelease(lambda: emitter.send(1))))
        emitter.remove_all()
        self.assertEqual(emitter.receiver_count, 0)

    def test_a_handler_released_when_its_receiver_is_compacted_may_send_to_its_emitter(self):
        emitter, log = Emitter(), []
        staying, gone = Probe(), Probe()
        emitter.on(staying, lambda r, v: log.append(v))
        emitter.on(gone, holding(OnRelease(lambda: emitter.send(5))))
        del gone
        emitter.send(0)
        self.assertEqual(log, [0, 5])

    def test_a_release_that_changes_its_emitter_does_not_trip_the_lock(self):
        emitter, log = Emitter(), []
        leaving, staying, late = Probe(), Probe(), Probe()
        emitter.on(staying, lambda r, v: log.append(v))
        emitter.on(leaving, holding(OnRelease(lambda: (emitter.on(late, lambda r, v: log.append(v * 10)),
                                                       emitter.off(late, key="none")))))
        emitter.off(leaving)
        emitter.send(1)
        self.assertEqual(log, [1, 10])
        emitter.on(leaving, holding(OnRelease(emitter.remove_all)))
        emitter.off(leaving)
        self.assertEqual(emitter.receiver_count, 0)

    def test_concurrent_send_subscribe_and_release_stay_consistent(self):
        emitter, delivered = Emitter(), []
        steady = Probe()
        emitter.on(steady, lambda r, v: delivered.append(v))

        def churn(base):
            for step in range(125):
                transient = Probe()
                emitter.on(transient, lambda r, v: None, key=f"transient {(base + step) % 7}")
                emitter.send(base + step)
                del transient

        threads = [threading.Thread(target=churn, args=(i * 125,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        emitter.send(-1)
        self.assertEqual(len(delivered), 1001)
        self.assertEqual(emitter.receiver_count, 1)


class Cycle:
    """A garbage cycle whose finalizer asks the kernel for something: what the cycle collector may run anywhere."""

    def __init__(self, emitter):
        self.me = self
        self.emitter = emitter

    def __del__(self):
        self.emitter.send("from a finalizer")


class LockRuleTests(unittest.TestCase):
    def test_a_second_lock_or_the_authors_code_under_a_lock_is_refused(self):
        first, second = Locked([], "the first lock"), Locked([], "the second lock")
        with self.assertRaisesRegex(LockRuleError, "took the second lock while holding the first lock"):
            first.with_(lambda _: second.get())
        with self.assertRaisesRegex(LockRuleError, "called the author's run while holding the first lock"):
            first.with_(lambda _: locked.check("called the author's run"))
        self.assertFalse(locked.held())

    def test_what_a_body_returns_is_let_go_after_the_unlock(self):
        guard = Locked(["kept"], "a list's lock")
        order = []
        removed = guard.with_(lambda items: [OnRelease(lambda: order.append(locked.held()))])
        del removed
        self.assertEqual(order, [False])
        self.assertEqual(guard.replace(["new"]), ["kept"])

    def test_a_finalizer_run_under_a_lock_has_its_send_deferred_to_the_unlock(self):
        emitter, log = Emitter(), []
        receiver = Probe()
        emitter.on(receiver, lambda r, v: log.append((v, locked.held())))
        guard = Locked(None, "a lock the collector runs under")
        Cycle(emitter)
        gc.disable()
        try:
            guard.with_(lambda _: gc.collect())
        finally:
            gc.enable()
        self.assertEqual(log, [("from a finalizer", False)], "the send ran once, after the lock was let go")


class MainTests(unittest.TestCase):
    def test_turns_run_on_main_in_order_and_calls_hand_back_values_and_errors(self):
        order = []
        for n in range(100):
            main.enqueue(order.append, n)
        main.turn()
        self.assertEqual(order, list(range(100)))
        self.assertTrue(main.call(main.is_current))
        self.assertFalse(main.is_current())
        with self.assertRaises(ZeroDivisionError):
            main.call(lambda: 1 / 0)

    def test_on_main_run_and_call_are_inline_and_enqueue_waits_its_turn(self):
        order = []

        def from_main():
            main.enqueue(order.append, 2)
            main.run(order.append, 1)
            main.call(order.append, 0)

        main.enqueue(from_main)
        main.turn()
        main.turn()
        self.assertEqual(order, [1, 0, 2])

    def test_each_senders_turns_keep_their_order_across_threads(self):
        order = []

        def send_many(base):
            for n in range(500):
                main.enqueue(order.append, (base, n))

        threads = [threading.Thread(target=send_many, args=(t,)) for t in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        main.turn()
        last = {}
        for base, n in order:
            self.assertGreater(n, last.get(base, -1))
            last[base] = n
        self.assertEqual(len(order), 4000)


if __name__ == "__main__":
    unittest.main()
