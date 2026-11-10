"""granite-answer/1, built, checked and stamped. GraniteRust's Answer and AnswerCheck (Swift's AgentAnswerCheck), rule
for rule and sentence for sentence, in the same order; GraniteC's answer.c.

PIN: AN ANSWER NAMES EVERY FILE IT WROTE. ``paths`` is always there (``[]`` when none), and every ``path``, ``made``
and ``payloads`` item points into it. ``format`` and ``agent`` are the runner's to stamp, never the package's.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

#: The standard an answer keeps.
FORMAT = "granite-answer/1"

_STAMPED = ("format", "agent")
_STATES = ("written", "rewritten", "unchanged", "removed")


class Make:
    """One kind of thing a package makes, as it declares it (``makes``): its kind, what it is, the folders it goes
    into, and what it never is."""

    __slots__ = ("kind", "what", "into", "never")

    def __init__(self, kind: str, what: str = "", into: Sequence[str] = (), never: str | None = None) -> None:
        self.kind, self.what, self.into, self.never = kind, what, tuple(into), never

    def entry(self) -> dict:
        entry: dict[str, Any] = {"kind": self.kind, "what": self.what, "into": list(self.into)}
        if self.never is not None:
            entry["never"] = self.never
        return entry


class Answer(dict):
    """An answer being built: a dict with ``path`` (None), ``paths``, ``made``, ``expected``, ``problems`` and
    ``payloads`` (each ``[]``) to start; the package's own keys sit beside them."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(path=None, paths=[], made=[], expected=[], problems=[], payloads=[])
        self.update(*args, **kwargs)

    def _list(self, key: str) -> list:
        items = self.get(key)
        if type(items) is not list:
            items = self[key] = []
        return items

    def path(self, path: str) -> "Answer":
        """The one file a person opens first; it is named in ``paths`` too."""
        self["path"] = path
        return self.add_path(path)

    def add_path(self, path: str) -> "Answer":
        """A file the call wrote."""
        self._list("paths").append(path)
        return self

    def note(self, sentence: str) -> "Answer":
        """A sentence about the answer, for the person."""
        self._list("notes").append(str(sentence))
        return self

    def made(self, kind: str, name: str, path: str | None, state: str = "written") -> "Answer":
        """A thing the call made, of a kind the package declares; written or rewritten ones name a file in ``paths``."""
        item = {"kind": kind, "name": name, "state": state}
        if path is not None:
            item["path"] = path
        self._list("made").append(item)
        return self

    def expected(self, kind: str, name: str) -> "Answer":
        """A thing the call meant to make and did not."""
        self._list("expected").append({"kind": kind, "name": name})
        return self

    def problem(self, kind: str, text: str) -> "Answer":
        """A problem the call found, under one lowercase word."""
        self._list("problems").append({"kind": kind, "text": text})
        return self

    def payload(self, path: str, schema: str) -> "Answer":
        """A JSON file in ``paths`` whose own ``schema`` says what it is."""
        self._list("payloads").append({"path": path, "schema": schema})
        return self


# --- paths as files -------------------------------------------------------------------------------------------------

def resolved(path: str, directory: str | None = None) -> str:
    """``path`` as a file: joined to ``directory`` (or the working folder) when relative, made lexical, then its
    longest ancestor that exists resolved through its links."""
    if path.startswith("/"):
        joined = path
    else:
        if directory is not None:
            base = directory
        else:
            try:
                base = os.getcwd()
            except OSError:
                base = "/"
        joined = f"{base}/{path}"
    parts: list[str] = []
    for part in joined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    whole = "/" + "/".join(parts)
    cut = len(whole)
    while True:
        prefix = whole[:cut] if cut else "/"
        try:
            real = os.path.realpath(prefix, strict=True)
        except (OSError, ValueError):
            real = None
        if real is not None:
            rest = whole[cut:]
            if real == "/":
                return rest or "/"
            return real + rest
        if cut == 0:
            return whole
        i = cut
        while i > 0 and whole[i - 1] != "/":
            i -= 1
        cut = i - 1 if i > 0 else 0


# --- the check ------------------------------------------------------------------------------------------------------

def _string(item: dict, key: str) -> str | None:
    value = item.get(key)
    return value if type(value) is str else None


def _lowercase_word(text: str) -> bool:
    if not text or not "a" <= text[0] <= "z":
        return False
    return all("a" <= c <= "z" or "0" <= c <= "9" or c == "-" for c in text[1:])


def problems(answer: dict, word: str, makes: Iterable[Make] = (), directory: str | None = None) -> list[str]:
    """What ``answer`` breaks of granite-answer/1, one sentence each, in AnswerCheck's order; ``[]`` when it keeps it."""
    makes = list(makes)
    kinds = {m.kind for m in makes}
    declared = ("makes " + ", ".join(f"`{m.kind}`" for m in makes)) if makes else \
        "declares nothing it makes (`makes`)"
    said: list[str] = []
    for key in _STAMPED:
        if key in answer:
            said.append(f"`{key}` is the runner's to say: an answer does not encode it")

    paths: list[str] = []
    if "paths" not in answer:
        said.append("`paths` is missing: an answer names every file it wrote, `[]` when none")
    elif type(answer["paths"]) is not list:
        said.append("`paths` is not a list of paths")
    else:
        for item in answer["paths"]:
            if type(item) is str:
                paths.append(resolved(item, directory))
            else:
                said.append("`paths` holds something that is not a path")
    known = set(paths)

    def in_paths(path: str) -> bool:
        return resolved(path, directory) in known

    if "path" in answer:
        path = answer["path"]
        if type(path) is str:
            if not in_paths(path):
                said.append(f"`path` {path} is not one of `paths`")
        elif path is not None:
            said.append("`path` is not a path")

    def made(item: dict, index: int) -> None:
        kind, name = _string(item, "kind"), _string(item, "name")
        if kind is None or not name:
            said.append(f"`made[{index}]` needs a `kind` and a `name`")
            return
        if kind not in kinds:
            said.append(f"`made[{index}]` is a `{kind}`, and {word} {declared}")
        state_text = _string(item, "state")
        state = state_text if state_text in _STATES else None
        if "state" in item and state is None:
            said.append(f"`made[{index}].state` is one of `written`, `rewritten`, `unchanged`, `removed`")
        if state is None or state in ("written", "rewritten"):
            path = _string(item, "path")
            if path is None:
                said.append(f"`made[{index}]` was written, so it names its `path`")
            elif not in_paths(path):
                said.append(f"`made[{index}]` is in {path}, which is not one of `paths`")

    def expected(item: dict, index: int) -> None:
        kind = _string(item, "kind")
        if kind is None or _string(item, "name") is None:
            said.append(f"`expected[{index}]` needs a `kind` and a `name`")
            return
        if kind not in kinds:
            said.append(f"`expected[{index}]` is a `{kind}`, and {word} {declared}")

    def problem(item: dict, index: int) -> None:
        kind = _string(item, "kind")
        if kind is None or _string(item, "text") is None:
            said.append(f"`problems[{index}]` needs a `kind` and a `text`")
            return
        if not _lowercase_word(kind):
            said.append(f"`problems[{index}].kind` is one lowercase word")

    def payload(item: dict, index: int) -> None:
        path, schema = _string(item, "path"), _string(item, "schema")
        if path is None or schema is None:
            said.append(f"`payloads[{index}]` needs a `path` and a `schema`")
            return
        file = resolved(path, directory)
        if file not in known:
            said.append(f"`payloads[{index}]` {path} is not one of `paths`")
            return
        from granite import agentjson
        try:
            with open(file, "rb") as opened:
                read = agentjson.read(opened.read())
        except (OSError, agentjson.JSONError):
            read = None
        if type(read) is not dict:
            said.append(f"`payloads[{index}]` {path} is not a JSON object on disk")
            return
        own = _string(read, "schema")
        if own is None:
            said.append(f"`payloads[{index}]` says `{schema}`, and the file's own `schema` is missing")
        elif own != schema:
            said.append(f"`payloads[{index}]` says `{schema}`, and the file's own `schema` is `{own}`")

    for key, one in (("made", made), ("expected", expected), ("problems", problem), ("payloads", payload)):
        if key not in answer:
            continue
        items = answer[key]
        if type(items) is not list:
            said.append(f"`{key}` is not a list")
            continue
        for index, item in enumerate(items):
            if type(item) is not dict:
                said.append(f"`{key}[{index}]` is not an object")
            else:
                one(item, index)

    if "notes" in answer:
        notes = answer["notes"]
        if type(notes) is not list or any(type(n) is not str for n in notes):
            said.append("`notes` is a list of sentences")
    return said


def stamped(answer: dict, word: str) -> dict:
    """``answer`` with the runner's ``format`` and ``agent``."""
    out = dict(answer)
    out["format"] = FORMAT
    out["agent"] = {"word": word}
    return out
