"""One installed runner, and where its code came from: ``installation.json``. GraniteRust's AgentInstallation;
GraniteC's installation.c.

PIN: A RECORD READS AS SERDE READS ITS STRUCT, without the position: keys visited in sorted order and the first that
does not read said, then the first required field missing, in declared order. One that does not parse is said
exactly as Rust says it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from granite import schema
from granite.host.source import Source

FORMAT = "granite-agent-installation/1"
FILE = "installation.json"


@dataclass
class Installation:
    name: str
    source: Source
    executable: str
    sha256: str
    size: int
    built_at: str
    entries: list = field(default_factory=list)
    format: str = FORMAT
    revision: str | None = None
    granite: str | None = None
    modified: float | None = None

    def json(self) -> dict:
        said: dict[str, Any] = {
            "format": self.format or FORMAT,
            "name": self.name,
            "source": self.source.json(),
            "executable": self.executable,
            "sha256": self.sha256,
            "size": self.size,
            "builtAt": self.built_at,
            "entries": list(self.entries or []),
        }
        if self.revision is not None:
            said["revision"] = self.revision
        if self.granite is not None:
            said["granite"] = self.granite
        if self.modified is not None:
            said["modified"] = float(self.modified)
        return said

    @property
    def word(self) -> str:
        """The word of its first entry, or its name."""
        first = self.entries[0] if self.entries else None
        word = first.get("word") if isinstance(first, dict) else None
        return word if type(word) is str else self.name

    @property
    def words(self) -> list[str]:
        return [e["word"] for e in self.entries if isinstance(e, dict) and type(e.get("word")) is str]

    @classmethod
    def read(cls, value: Any) -> "Installation":
        """A record read from its JSON value; raises ``ValueError`` with serde's sentence."""
        if type(value) is not dict:
            raise ValueError(schema.invalid_type(value, "struct AgentInstallation"))
        read: dict[str, Any] = {}
        for key in sorted(value):
            if key not in _FIELDS:
                continue
            shape, required = _FIELDS[key]
            member = value[key]
            if not required and member is None:
                read[key] = None
                continue
            if key == "source":
                read[key] = _source(member)
                continue
            said = shape.check(member)
            if said is not None:
                raise ValueError(said)
            read[key] = member
        for key, (_, required) in _FIELDS.items():
            if required and key not in value:
                raise ValueError(f"missing field `{key}`")
        return cls(name=read["name"], source=read["source"], executable=read["executable"], sha256=read["sha256"],
                   size=read["size"], built_at=read["builtAt"], entries=read["entries"], format=read["format"],
                   revision=read.get("revision"), granite=read.get("granite"),
                   modified=float(read["modified"]) if read.get("modified") is not None else None)


def _source(value: Any) -> Source:
    """AgentSource, an untagged enum: a folder, or a repository at an optional revision."""
    if type(value) is dict:
        if type(value.get("path")) is str:
            return Source(path=value["path"])
        revision = value.get("revision")
        if type(value.get("url")) is str and (revision is None or type(revision) is str):
            return Source(url=value["url"], revision=revision)
    raise ValueError("data did not match any variant of untagged enum AgentSource")


_STRING = schema.Schema("string")
# In the order the struct declares them, as a missing field is said.
_FIELDS: dict[str, tuple[schema.Schema | None, bool]] = {
    "format": (_STRING, True),
    "name": (_STRING, True),
    "source": (None, True),
    "revision": (_STRING, False),
    "granite": (_STRING, False),
    "executable": (_STRING, True),
    "sha256": (_STRING, True),
    "size": (schema.of(schema.u64), True),
    "modified": (schema.Schema("number"), False),
    "builtAt": (_STRING, True),
    "entries": (schema.Schema("array", items=schema.ANY), True),
}
