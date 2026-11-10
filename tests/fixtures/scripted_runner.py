"""A runner that says what a test scripts, byte for byte: the edge cases a host meets and a real agent never says. The
scenario is GRANITE_SCRIPT, since a host passes a runner no arguments for a call. Test-only; GraniteC's
scripted_runner.c.

Python ignores SIGPIPE itself as it starts, so ``report`` cannot see whether its host left SIGPIPE ignored: it says
the other four. GraniteC's scripted runner, driven by this host in the interop check, sees all five.
"""

import fcntl
import os
import signal
import sys
import time


def say(text: str) -> None:
    data = text.encode()
    while data:
        data = data[os.write(1, data):]


def hello(wire: str) -> None:
    say('{"hello":{"pid":%d,"wire":"%s"}}\n' % (os.getpid(), wire))


def read_perform() -> None:
    while True:
        c = os.read(0, 1)
        if not c or c == b"\n":
            return


def forever() -> None:
    while True:
        signal.pause()


def report() -> None:
    """What the runner inherited from its host: blocked signals, ignored ones, whether it leads its own group, its
    folder, its open descriptors past stderr, and GRANITE_AGENT."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
    from granite import agentjson

    blocked = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []) if int(s) < 32)
    ignored = [int(s) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)
               if signal.getsignal(s) == signal.SIG_IGN]
    fds = []
    for fd in range(3, 256):
        try:
            fcntl.fcntl(fd, fcntl.F_GETFD)
            fds.append(fd)
        except OSError:
            pass
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = "?"
    said = {"blocked": blocked, "ignored": ignored, "leads": os.getpgrp() == os.getpid(), "cwd": cwd, "fds": fds,
            "agent": os.environ.get("GRANITE_AGENT", "")}
    say(agentjson.write({"output": said}).decode() + "\n")


def main() -> int:
    script = os.environ.get("GRANITE_SCRIPT", "report")
    if script == "exit-early":
        return 7
    if script == "misplaced":
        say('{"output":{}}\n')
        forever()
    if script == "major2":
        hello("granite-agent/2")
        forever()
    if script == "noisy":
        say('not json\n{"weather":1}\n\n')
    hello("granite-agent/1")
    read_perform()
    if script == "report":
        report()
    elif script == "noisy":
        say('{"output":{"heard":true}}\n')
    elif script == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        say('{"note":"ignoring SIGTERM"}\n')
        forever()
    elif script == "grandchild":
        child = os.fork()
        if child == 0:
            while True:
                time.sleep(60)
        say('{"output":{"grandchild":%d}}\n' % child)
    elif script == "silent":
        return 0
    elif script == "long-note":
        say('{"note":"' + "x" * 100000 + '"}\n')
        say('{"output":{"long":true}}\n')
    elif script == "hang":
        forever()
    return 0


if __name__ == "__main__":
    os._exit(main())
