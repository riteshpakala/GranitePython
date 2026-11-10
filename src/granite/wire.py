"""The messages of granite-agent/1: one JSON object per line, sorted. GraniteRust's AgentWire; GraniteC's wire.c.

Reading follows AgentWire::read: keys tried in Swift Granite's order, then each message read as serde reads its
struct. Fields are visited in key order (serde_json's map is sorted) and the first that does not read is said; then
the first required field missing, in the order the struct declares them. Every sentence is prefixed with the
message's key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

from granite import agentjson, schema
from granite.error import Cancelled, Fault, GraniteError, Refusal, kind_named
from granite.schema import i32, u32, u64

WIRE = "granite-agent/1"
ANSWER_STANDARD = "granite-answer/1"
KINDS = ("hello", "perform", "note", "model", "answer", "output", "failed", "cancel", "entries")


class WireError(ValueError):
    """A line that is not a message, and why, in Rust's sentence."""

    def __init__(self, said: str) -> None:
        super().__init__(said)
        self.said = said

    def __str__(self) -> str:
        return self.said


# --- the messages, each as serde reads its struct -------------------------------------------------------------------

@dataclass
class Hello:
    """A runner's first line: the wire it speaks, its process, and the answer standard it keeps."""

    wire: str = WIRE
    pid: i32 | None = None
    answer: str | None = None


@dataclass
class Perform:
    """A host's call: the word, its input, and what the host offers."""

    word: str
    input: Any
    directory: str | None = None
    ask: str | None = None
    model: bool = False


@dataclass
class Note:
    """A line of progress."""

    text: str


@dataclass
class ModelAsk:
    """A runner asking its host's model: for an object that fits ``schema``, or for text."""

    id: u64
    kind: Literal["object", "text"]
    schema: Any | None
    system: str
    user: str
    max_tokens: u32
    seconds: float


@dataclass
class ModelAnswer:
    """A host's answer to a model ask: an object, text, or why there is none."""

    id: u64
    object: Any | None = None
    text: str | None = None
    error: str | None = None


@dataclass
class Output:
    """A call's answer."""

    value: Any


@dataclass
class Failed:
    """A call that was not answered: a refusal (its kind and reasons cross too), or a fault."""

    error: GraniteError


@dataclass
class Cancel:
    """A host stopping the call."""


@dataclass
class Entries:
    """A runner's roster, as ``--describe`` says it."""

    entries: list = field(default_factory=list)


@dataclass
class Entry:
    kind: Literal["package", "worker"]
    word: str
    summary: str
    module: str
    type: str
    schema: Any | None = None
    fields: Any | None = None
    lanes: u32 | None = None
    guide: str | None = None
    makes: Any | None = None
    tool_names: Any | None = None
    budget: Any | None = None
    shell: bool | None = None
    report_rule: str | None = None


Message = Union[Hello, Perform, Note, ModelAsk, ModelAnswer, Output, Failed, Cancel, Entries]

_STRUCTS = {"hello": Hello, "perform": Perform, "model": ModelAsk, "answer": ModelAnswer}
_ENTRIES = schema.Schema("array", items=schema.of(Entry))
_STRING = schema.Schema("string")


# --- writing --------------------------------------------------------------------------------------------------------

def _body(message: Message) -> tuple[str, Any, dict]:
    """The message's key, its body, and the keys beside it."""
    if isinstance(message, Hello):
        body: dict[str, Any] = {"wire": message.wire}
        if message.pid is not None:
            body["pid"] = message.pid
        if message.answer is not None:
            body["answer"] = message.answer
        return "hello", body, {}
    if isinstance(message, Perform):
        body = {"word": message.word, "input": message.input, "model": bool(message.model)}
        if message.directory is not None:
            body["directory"] = message.directory
        if message.ask is not None:
            body["ask"] = message.ask
        return "perform", body, {}
    if isinstance(message, Note):
        return "note", message.text, {}
    if isinstance(message, ModelAsk):
        body = {"id": message.id, "kind": message.kind, "system": message.system, "user": message.user,
                "maxTokens": message.max_tokens, "seconds": agentjson.number(message.seconds)}
        if message.schema is not None:
            body["schema"] = message.schema
        return "model", body, {}
    if isinstance(message, ModelAnswer):
        body = {"id": message.id}
        for key in ("object", "text", "error"):
            if getattr(message, key) is not None:
                body[key] = getattr(message, key)
        return "answer", body, {}
    if isinstance(message, Output):
        return "output", message.value, {}
    if isinstance(message, Failed):
        error = message.error
        beside = {}
        if isinstance(error, Refusal):
            beside["refused"] = {"kind": error.kind, "reasons": list(error.reasons)}
        return "failed", error.said, beside
    if isinstance(message, Cancel):
        return "cancel", {}, {}
    if isinstance(message, Entries):
        return "entries", message.entries, {}
    raise TypeError(f"granite: a {type(message).__name__} is not a message")


def line(message: Message) -> bytes:
    """The message as one sorted line, ending in a newline."""
    key, body, beside = _body(message)
    return agentjson.write({key: body, **beside}) + b"\n"


def hello(pid: int, keeps_answers: bool = True) -> Hello:
    return Hello(WIRE, pid, ANSWER_STANDARD if keeps_answers else None)


def failed(error: GraniteError) -> Failed:
    """A failed line for ``error``: a cancel crosses as a fault saying so."""
    return Failed(Fault(error.said) if isinstance(error, Cancelled) else error)


# --- reading --------------------------------------------------------------------------------------------------------

def _refusal(refused: Any, said: str) -> Refusal | None:
    """A refusal as it crosses the wire, or None when it does not read (and is dropped)."""
    if type(refused) is not dict:
        return None
    kind, reasons = refused.get("kind"), refused.get("reasons")
    if type(kind) is not str or type(reasons) is not list or any(type(r) is not str for r in reasons):
        return None
    return Refusal(kind_named(kind), reasons=reasons, said=said)


def _struct(map: dict, key: str) -> Any:
    try:
        return schema.of(_STRUCTS[key]).decode(map[key])
    except ValueError as error:
        raise WireError(f"`{key}`: {error}") from None


def read(text: bytes | str) -> Message:
    """The message on ``text`` (one line, its newline optional). Raises ``WireError`` saying why it is not one."""
    if isinstance(text, str):
        text = text.encode("utf-8", "surrogatepass")
    end = len(text)
    while end and text[end - 1] == 0x0A:
        end -= 1
    try:
        map = agentjson.read(text[:end])
    except agentjson.JSONError as error:
        raise WireError(str(error)) from None
    if type(map) is not dict:
        raise WireError("a message is a JSON object")

    def present(key: str) -> bool:
        return map.get(key) is not None

    if present("hello"):
        return _struct(map, "hello")
    if present("perform"):
        return _struct(map, "perform")
    if present("note"):
        said = _STRING.check(map["note"])
        if said is not None:
            raise WireError(f"`note`: {said}")
        return Note(map["note"])
    if present("model"):
        return _struct(map, "model")
    if present("answer"):
        return _struct(map, "answer")
    if "output" in map:
        return Output(map["output"])
    if present("failed"):
        sentence = map["failed"]
        said = _STRING.check(sentence)
        if said is not None:
            raise WireError(f"`failed`: {said}")
        return Failed(_refusal(map.get("refused"), sentence) or Fault(sentence))
    if "cancel" in map:
        return Cancel()
    if present("entries"):
        said = _ENTRIES.check(map["entries"])
        if said is not None:
            raise WireError(f"`entries`: {said}")
        return Entries(map["entries"])
    raise WireError("a message is one of " + ", ".join(KINDS))
