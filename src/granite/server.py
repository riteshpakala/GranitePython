"""The agent's side of granite-agent/1: GraniteRust's AgentServer and runner main; GraniteC's server.c.

One call: ``hello``, then the host's ``perform`` started on the roster's package on a thread of its own, the host's
answers matched to the run's asks of its model, a ``cancel`` or the host's leaving handled, and exactly one ``output``
or ``failed`` said at the end, in GraniteRust's sentences. Exits 0 answered, 1 failed, 2 the host spoke out of turn
or left, 3 refused, 130 cancelled.

PIN: THE WIRE IS STDOUT'S OWN DESCRIPTOR, KEPT FROM CHILDREN. What the process calls stdout is stderr (``print`` stays
in order and off the wire), and stdin is the channel's, with /dev/null where it was.

PIN: SIGTERM CANCELS THE CALL, through a wakeup pipe read on a thread of its own: no blocked mask is left for an
agent's children to inherit, and no Python handler has to wait for the main thread.

PIN: THE LAST LINE IS THE LAST THING. The runner leaves through ``os._exit`` once its last line is written and stderr
flushed, so no thread still running can write after it.
"""

from __future__ import annotations

import fcntl
import os
import signal
import sys
import threading
import traceback
from typing import Any, NoReturn, Sequence

from granite import agentjson, wire
from granite.channel import Channel
from granite.context import CancelFlag, Host, HostModel
from granite.core.emitter import Anchor
from granite.error import Cancelled, Fault, GraniteError, Refusal, host_no_answer
from granite.package import Roster, perform

ANSWERED, FAILED, UNREADABLE, REFUSED, CANCELLED = 0, 1, 2, 3, 130

_KEYS = {wire.Hello: "hello", wire.Perform: "perform", wire.Note: "note", wire.ModelAsk: "model",
         wire.ModelAnswer: "answer", wire.Output: "output", wire.Failed: "failed", wire.Cancel: "cancel",
         wire.Entries: "entries"}


# --- the host's model, asked across the channel ---------------------------------------------------------------------

class _Wait:
    __slots__ = ("id", "answer", "cancelled", "__weakref__")

    def __init__(self, id: int) -> None:
        self.id = id
        self.answer: wire.ModelAnswer | None = None
        self.cancelled = False


class ChannelModel(HostModel):
    """The host's model, asked with a ``model`` line and answered with an ``answer`` line of the same id."""

    def __init__(self, channel: Channel) -> None:
        self._channel = channel
        self._answered = threading.Condition()
        self._next_id = 0
        self._waiting: list[_Wait] = []
        self._gone = False

    def resolve(self, answer: wire.ModelAnswer) -> None:
        """The host's answer to an ask; one nobody waits for is dropped."""
        with self._answered:
            for wait in self._waiting:
                if wait.id == answer.id and wait.answer is None:
                    wait.answer = answer
                    self._answered.notify_all()
                    return

    def leave(self) -> None:
        """The host is gone: every ask waiting, and every ask after, ends without an answer."""
        with self._answered:
            self._gone = True
            self._answered.notify_all()

    def _ask(self, message: wire.ModelAsk, cancel: CancelFlag | None) -> wire.ModelAnswer:
        with self._answered:
            if self._gone:
                raise host_no_answer("the host is gone")
            self._next_id += 1
            wait = _Wait(self._next_id)
            self._waiting.append(wait)
        message.id = wait.id
        answered = self._answered

        def wake(waiting: _Wait, value: None) -> None:
            with answered:
                waiting.cancelled = True
                answered.notify_all()

        if cancel is not None:
            cancel.on(wait, wake)
        why = self._channel.send(message)
        with self._answered:
            while why is None and wait.answer is None and not wait.cancelled and not self._gone:
                self._answered.wait()
            self._waiting.remove(wait)
            got, cancelled = wait.answer, wait.cancelled
        if why is not None:
            raise host_no_answer(f"the ask could not be sent: {why}")
        if got is not None:
            return got
        raise host_no_answer("the call was cancelled" if cancelled else "the host is gone")

    def object(self, schema: Any, system: str, user: str, max_tokens: int, seconds: float,
               cancel: CancelFlag | None = None) -> Any:
        answer = self._ask(wire.ModelAsk(0, "object", agentjson.copy(schema), system, user, max_tokens, seconds),
                           cancel)
        if answer.error is not None:
            raise host_no_answer(answer.error)
        if answer.object is None:
            raise host_no_answer("the host answered no object")
        return answer.object

    def text(self, system: str, user: str, max_tokens: int, seconds: float, cancel: CancelFlag | None = None) -> str:
        answer = self._ask(wire.ModelAsk(0, "text", None, system, user, max_tokens, seconds), cancel)
        if answer.error is not None:
            raise host_no_answer(answer.error)
        return answer.text if answer.text is not None else ""


# --- one call -------------------------------------------------------------------------------------------------------

class _Call:
    def __init__(self, channel: Channel, roster: Roster) -> None:
        self.channel = channel
        self.roster = roster
        self.lock = threading.Condition()
        self.started = self.cancelled = self.ended = False
        self.exit = FAILED
        self.model: ChannelModel | None = None
        self.cancel = CancelFlag()  # the run's: set by a cancel

    def finish(self, exit: int, message: wire.Message) -> None:
        """Says ``message`` and ends the call with ``exit``, once."""
        with self.lock:
            first = not self.ended
            self.ended = True
            if first:
                self.exit = exit
        if not first:
            return
        self.channel.send(message)
        with self.lock:
            self.lock.notify_all()

    def finish_said(self, exit: int, said: str) -> None:
        self.finish(exit, wire.failed(Fault(said)))

    def cancel_call(self) -> None:
        """Ends the call: a run is stopped, and says so; before a run, the call ends as cancelled."""
        with self.lock:
            self.cancelled = True
            started = self.started
        self.cancel.set()
        if not started:
            self.finish_said(CANCELLED, "cancelled before the call began")

    def run(self, package: Any, message: wire.Perform, host: Host) -> None:
        try:
            try:
                answer = perform(package, agentjson.write(message.input), host, self.cancel)
            except Cancelled:
                self.finish_said(CANCELLED, "cancelled")
                return
            except GraniteError as error:
                with self.lock:
                    cancelled = self.cancelled
                exit = CANCELLED if cancelled else REFUSED if isinstance(error, Refusal) else FAILED
                self.finish(exit, wire.failed(error))
                return
            self.finish(ANSWERED, wire.Output(agentjson.read(answer)))
        except BaseException as error:  # the kernel's own fault: said, so the call still ends
            sys.stderr.write("granite: a call raised:\n" + traceback.format_exc())
            self.finish_said(FAILED, f"{package.word} failed: {error}")

    def start(self, message: wire.Perform) -> None:
        with self.lock:
            again = self.started or self.ended
            self.started = True
        if again:
            self.finish_said(UNREADABLE, "a process answers one call; the host sent a second `perform`")
            return
        package = self.roster.find(message.word)
        if package is None:
            carried = ", ".join(f"`{e['word']}`" for e in self.roster.entries()) or "none"
            self.finish_said(FAILED, f"no agent is called `{message.word}` in this process; it carries {carried}")
            return
        model = ChannelModel(self.channel) if message.model else None
        channel = self.channel
        host = Host(message.directory, message.ask, model, lambda line: channel.send(wire.Note(line)))
        with self.lock:
            self.model = model
            cancelled = self.cancelled
        if cancelled:
            self.cancel.set()
        try:
            threading.Thread(target=self.run, args=(package, message, host), name="granite.call",
                             daemon=True).start()
        except RuntimeError as error:
            self.finish_said(FAILED, f"the call could not start a thread: {error}")

    def listen(self) -> None:
        """Reads the host until it has said all it will."""
        try:
            while (message := self.channel.recv()) is not None:
                if isinstance(message, wire.Perform):
                    self.start(message)
                elif isinstance(message, wire.ModelAnswer):
                    with self.lock:
                        model = self.model
                    if model is not None:
                        model.resolve(message)
                elif isinstance(message, wire.Cancel):
                    self.cancel_call()
                else:
                    self.finish_said(UNREADABLE, f"the host said `{_KEYS[type(message)]}`, which is not a host's "
                                                 "message")
            # The host sends nothing more: a call it made runs on, but no ask of the model can be answered. A host
            # that closes its end at once (echo '{"perform": …}' | runner) is served; only `cancel` or SIGTERM
            # cancels. A host that left before calling ends the process.
            with self.lock:
                model, called = self.model, self.started or self.ended
            if model is not None:
                model.leave()
            if not called:
                self.finish_said(UNREADABLE, "the host left before making a call")
        except BaseException as error:
            sys.stderr.write("granite: the server's listener raised:\n" + traceback.format_exc())
            self.finish_said(FAILED, f"the server stopped listening: {error}")


def serve_channel(channel: Channel, roster: Roster, cancel: CancelFlag | None = None) -> int:
    """Answers one call on ``channel`` and returns its exit. Setting ``cancel`` cancels it."""
    call = _Call(channel, roster)
    channel.send(wire.hello(os.getpid(), True))
    watching = None
    if cancel is not None:
        watching = Anchor()
        cancel.on(watching, lambda receiver, value: call.cancel_call())
    # The listener outlives the call when the host never closes its end: the process exits under it.
    threading.Thread(target=call.listen, name="granite.listen", daemon=True).start()
    with call.lock:
        while not call.ended:
            call.lock.wait()
        exit = call.exit
    del watching
    return exit


# --- the process ----------------------------------------------------------------------------------------------------

def _unreadable(line: bytes, why: str) -> None:
    sys.stderr.write(f"granite-agent: an unreadable line from the host: {line.decode('utf-8', 'replace')}\n")


def _watch_terminate(read_fd: int, terminated: CancelFlag) -> None:
    try:
        while True:
            got = os.read(read_fd, 64)
            if not got or signal.SIGTERM in got:
                break
    except OSError:
        pass
    terminated.set()


def _ignore(signal_number: int, frame: Any) -> None:
    """SIGTERM's Python handler does nothing: the wakeup pipe says it."""


def serve(roster: Roster | Sequence[Any], argv: Sequence[str] | None = None) -> NoReturn:
    """A runner's main: ``--describe`` says the roster's entries; otherwise one call is answered on stdin and stdout.
    Never returns."""
    if not isinstance(roster, Roster):
        roster = Roster(*roster)
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    # The wire is stdout's own descriptor, kept from children; what the process calls stdout is stderr; stdin is the
    # channel's, with /dev/null where it was.
    try:
        wire_fd = fcntl.fcntl(1, fcntl.F_DUPFD_CLOEXEC, 3)
        reading = fcntl.fcntl(0, fcntl.F_DUPFD_CLOEXEC, 3)
    except OSError as error:
        sys.stderr.write(f"granite-agent: the wire could not be set aside: {error.strerror}\n")
        sys.stderr.flush()
        os._exit(FAILED)
    os.dup2(2, 1)
    try:
        null = os.open("/dev/null", os.O_RDONLY)
        os.dup2(null, 0)
        if null != 0:
            os.close(null)
    except OSError:
        pass
    sys.stdout = sys.stderr
    channel = Channel(reading, wire_fd, _unreadable)

    if "--describe" in argv:
        channel.send(wire.Entries(roster.entries()))
        channel.close()
        _leave(ANSWERED)

    # SIGTERM cancels the call; SIGINT ends the process as its default does.
    terminated = CancelFlag()
    if threading.current_thread() is threading.main_thread():
        read_fd, write_fd = os.pipe()
        os.set_blocking(write_fd, False)
        signal.signal(signal.SIGTERM, _ignore)
        signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        threading.Thread(target=_watch_terminate, args=(read_fd, terminated), name="granite.term",
                         daemon=True).start()
    code = serve_channel(channel, roster, terminated)
    channel.close()
    _leave(code)


def _leave(code: int) -> NoReturn:
    try:
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    os._exit(code)
