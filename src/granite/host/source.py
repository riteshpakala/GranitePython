"""Where an agent's code comes from: a repository, at a revision or its newest, or a folder. GraniteRust's
AgentSource; GraniteC's granite_source."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """A folder (``path``), or a repository (``url``) at a ``revision`` (None: its newest)."""

    path: str | None = None
    url: str | None = None
    revision: str | None = None

    @classmethod
    def parse(cls, said: str, base: str | None = None) -> "Source":
        """What a person says: a path (``/``, ``~`` or ``.`` first) made absolute against ``base``, or a URL with an
        optional ``@revision`` after its last ``/``."""
        trimmed = said.strip(" \t\n")
        if trimmed[:1] in ("/", "~", "."):
            if trimmed.startswith("~"):
                joined = f"{os.environ.get('HOME', '')}/{trimmed[1:].lstrip('/')}"
            elif trimmed.startswith("/"):
                joined = trimmed
            else:
                joined = f"{base if base is not None else os.getcwd()}/{trimmed}"
            parts: list[str] = []
            for part in joined.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    if parts:
                        parts.pop()
                    continue
                parts.append(part)
            return cls(path="/" + "/".join(parts))
        # A revision follows the last @ after the last /: git@host:x/y.git has none.
        at, slash = trimmed.rfind("@"), trimmed.rfind("/")
        if at >= 0 and slash >= 0 and at > slash:
            return cls(url=trimmed[:at], revision=trimmed[at + 1:])
        return cls(url=trimmed)

    def newest(self) -> "Source":
        """The same source without its revision: what an update fetches."""
        return Source(self.path, self.url, None)

    @property
    def text(self) -> str:
        if self.path is not None:
            return self.path
        if self.revision is not None:
            return f"{self.url}@{self.revision}"
        return self.url or ""

    def json(self) -> dict:
        if self.path is not None:
            return {"path": self.path}
        said = {"url": self.url or ""}
        if self.revision is not None:
            said["revision"] = self.revision
        return said
