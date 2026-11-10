"""What a package's input takes, read off a dataclass: the data form a host reads (``describe``), the JSON Schema a
model is asked to fit, and the input decoded with serde's sentences. GraniteRust's GraniteSchema and its typed
deserializer; GraniteC's schema.c.

An input is a dataclass whose fields are annotated with ``str``, ``bool``, ``int`` (an i64; ``granite.u32``,
``granite.u64`` and ``granite.i32`` say another width, as Rust's types do), ``float`` (an f64), ``Literal["a", "b"]`` (a
choice), ``list[T]``, another dataclass, ``Any``, or any of those ``| None``. A field is required unless it is ``| None``;
a default does not make it optional, as in Rust. Its wire name is its attribute's in camelCase (``ask_model`` reads
``askModel``), as Rust's ``rename_all = "camelCase"``; ``granite.field(name=..., note=...)`` says another name and the
line said beside the field.

PIN: SERDE'S WORDS, WITHOUT ITS POSITION. A value that does not fit is said as serde_json says it, from a value
(``missing field `text```, ``invalid type: integer `5`, expected a string``): a map's keys are visited in sorted order
and the first field that does not read is said, then the first required field missing, in declared order.
"""

from __future__ import annotations

import dataclasses
import decimal
import math
import threading
import types
import typing
from typing import Annotated, Any, Literal, Union

#: The field every package that writes files takes: the folder it works in.
DIRECTORY_FIELD = "directory"


class SchemaError(TypeError):
    """A type a package input cannot take, said with its fix."""


class _Width:
    __slots__ = ("name", "low", "high")

    def __init__(self, name: str, low: int, high: int) -> None:
        self.name, self.low, self.high = name, low, high

    def __repr__(self) -> str:
        return self.name


i64 = int
u32 = Annotated[int, _Width("u32", 0, 2**32 - 1)]
u64 = Annotated[int, _Width("u64", 0, 2**64 - 1)]
i32 = Annotated[int, _Width("i32", -(2**31), 2**31 - 1)]
_I64 = _Width("i64", -(2**63), 2**63 - 1)


def field(*, name: str | None = None, note: str | None = None, default: Any = dataclasses.MISSING,
          default_factory: Any = dataclasses.MISSING) -> Any:
    """A dataclass field with a wire name and a note: ``text: str = granite.field(note="what to say")``."""
    metadata = {"granite": {"name": name, "note": note}}
    if default_factory is not dataclasses.MISSING:
        return dataclasses.field(default_factory=default_factory, metadata=metadata)
    if default is not dataclasses.MISSING:
        return dataclasses.field(default=default, metadata=metadata)
    return dataclasses.field(metadata=metadata)


def camel(name: str) -> str:
    """``ask_model`` as Rust's camelCase renames it: ``askModel``."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


class Field:
    """One field of an object: its wire name, the attribute it fills, whether it is required, its schema, its note."""

    __slots__ = ("name", "attribute", "required", "schema", "note", "default")

    def __init__(self, name: str, attribute: str, required: bool, schema: "Schema", note: str | None,
                 default: Any) -> None:
        self.name, self.attribute, self.required = name, attribute, required
        self.schema, self.note, self.default = schema, note, default


class Schema:
    """One shape: ``string``, ``integer``, ``number``, ``boolean``, ``any``, ``choice``, ``array`` or ``object``."""

    __slots__ = ("kind", "width", "choices", "items", "fields", "type")

    def __init__(self, kind: str, *, width: _Width | None = None, choices: tuple[str, ...] = (),
                 items: "Schema | None" = None, fields: tuple[Field, ...] = (), type: Any = None) -> None:
        self.kind, self.width, self.choices = kind, width, choices
        self.items, self.fields, self.type = items, fields, type

    # --- the forms a host reads ---------------------------------------------------------------------------------

    def value(self) -> Any:
        """The data form a host reads in ``describe``: ``"string"``, ``{"choice": [...]}``, ``{"array": ...}``,
        ``{"object": [{"name", "required", "schema"}]}``."""
        if self.kind == "choice":
            return {"choice": list(self.choices)}
        if self.kind == "array":
            return {"array": self.items.value() if self.items else "any"}
        if self.kind == "object":
            return {"object": [{"name": f.name, "required": f.required, "schema": f.schema.value()}
                               for f in self.fields]}
        return self.kind

    def json_schema(self, hide_directory: bool = False) -> dict:
        """The JSON Schema a model is asked to fit; ``hide_directory`` leaves out the host's own field."""
        if self.kind == "any":
            return {}
        if self.kind == "choice":
            return {"type": "string", "enum": list(self.choices)}
        if self.kind == "array":
            return {"type": "array", "items": self.items.json_schema() if self.items else {}}
        if self.kind == "object":
            properties, required = {}, []
            for f in self.fields:
                if hide_directory and f.name == DIRECTORY_FIELD:
                    continue
                shape = f.schema.json_schema()
                if f.note is not None:
                    shape["description"] = f.note
                properties[f.name] = shape
                if f.required:
                    required.append(f.name)
            return {"type": "object", "properties": properties, "required": required}
        return {"type": self.kind}

    def notes(self) -> dict[str, str]:
        """Each field's note, by wire name: a package's ``fields``."""
        return {f.name: f.note for f in self.fields if f.note is not None}

    @property
    def takes_directory(self) -> bool:
        return self.kind == "object" and any(f.name == DIRECTORY_FIELD for f in self.fields)

    # --- reading a value ----------------------------------------------------------------------------------------

    def check(self, value: Any) -> str | None:
        """Why ``value`` does not read as this shape, in serde's sentence; None when it reads."""
        try:
            self._decode(value, False)
        except _Unfit as unfit:
            return unfit.said
        return None

    def decode(self, value: Any) -> Any:
        """``value`` read as this shape: a dataclass for an object. Raises ``ValueError`` with serde's sentence."""
        try:
            return self._decode(value, True)
        except _Unfit as unfit:
            raise ValueError(unfit.said) from None

    def _decode(self, value: Any, build: bool) -> Any:
        kind = self.kind
        if kind == "any":
            return value
        if kind == "string":
            if type(value) is not str:
                raise _Unfit(invalid_type(value, "a string"))
            return value
        if kind == "boolean":
            if type(value) is not bool:
                raise _Unfit(invalid_type(value, "a boolean"))
            return value
        if kind == "integer":
            width = self.width or _I64
            if type(value) is not int:
                raise _Unfit(invalid_type(value, width.name))
            if not width.low <= value <= width.high:
                raise _Unfit(invalid_value(value, width.name))
            return value
        if kind == "number":
            if type(value) is int:
                return float(value) if build else value
            if type(value) is not float:
                raise _Unfit(invalid_type(value, "f64"))
            return value
        if kind == "choice":
            if type(value) is not str:
                raise _Unfit(invalid_type(value, "string or map"))
            if value not in self.choices:
                raise _Unfit(unknown_variant(value, self.choices))
            return value
        if kind == "array":
            if type(value) is not list:
                raise _Unfit(invalid_type(value, "a sequence"))
            items = self.items
            return [items._decode(item, build) if items else item for item in value]
        # an object: serde visits the map's keys in sorted order, then names the first required field it did not see
        if type(value) is not dict:
            raise _Unfit(invalid_type(value, f"struct {self.type.__name__}" if self.type else "a map"))
        by_name = {f.name: f for f in self.fields}
        read: dict[str, Any] = {}
        for key in sorted(value):
            f = by_name.get(key)
            if f is None:
                continue
            member = value[key]
            if not f.required and member is None:  # an Option reads null as none
                read[f.attribute] = None
                continue
            read[f.attribute] = f.schema._decode(member, build)
        for f in self.fields:
            if f.required and f.name not in value:
                raise _Unfit(f"missing field `{f.name}`")
        if not build:
            return None
        for f in self.fields:
            if f.attribute not in read:
                read[f.attribute] = f.default() if callable(f.default) else None
        return self.type(**read) if self.type else read


class _Unfit(Exception):
    def __init__(self, said: str) -> None:
        self.said = said


# --- serde's sentences ----------------------------------------------------------------------------------------------

def _float_display(value: float) -> str:
    """A double as Rust's Display writes it, which never uses an exponent, with serde's decimal point."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "-inf" if value < 0 else "inf"
    sign = "-" if math.copysign(1.0, value) < 0 else ""
    magnitude = abs(value)
    if magnitude == 0:
        return sign + "0.0"
    _, places, power = decimal.Decimal(repr(magnitude)).as_tuple()
    digits = "".join(map(str, places)).lstrip("0")
    stripped = digits.rstrip("0")
    power += len(digits) - len(stripped)
    digits = stripped
    exponent = power + len(digits) - 1
    if exponent < 0:
        return sign + "0." + "0" * (-exponent - 1) + digits
    if exponent + 1 >= len(digits):
        return sign + digits + "0" * (exponent + 1 - len(digits)) + ".0"
    return sign + digits[: exponent + 1] + "." + digits[exponent + 1:]


def _debug_string(text: str) -> str:
    """A string as Rust's ``{:?}`` quotes it."""
    out = ['"']
    for c in text:
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif c == "\n":
            out.append("\\n")
        elif c == "\r":
            out.append("\\r")
        elif c == "\t":
            out.append("\\t")
        elif c == "\0":
            out.append("\\0")
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append("\\u{%x}" % ord(c))
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


def unexpected(value: Any) -> str:
    """What serde calls a value it did not expect: ``integer `5```, ``string "x"``, ``map``."""
    if value is None:
        return "null"
    if type(value) is bool:
        return f"boolean `{'true' if value else 'false'}`"
    if type(value) is int:
        return f"integer `{value}`"
    if type(value) is float:
        return f"floating point `{_float_display(value)}`"
    if type(value) is str:
        return "string " + _debug_string(value)
    if type(value) is list:
        return "sequence"
    return "map"


def invalid_type(value: Any, expected: str) -> str:
    return f"invalid type: {unexpected(value)}, expected {expected}"


def invalid_value(value: Any, expected: str) -> str:
    return f"invalid value: {unexpected(value)}, expected {expected}"


def unknown_variant(got: str, choices: typing.Sequence[str]) -> str:
    """serde's sentence for a variant it does not know."""
    said = f"unknown variant `{got}`, "
    if not choices:
        return said + "there are no variants"
    if len(choices) == 1:
        return said + f"expected `{choices[0]}`"
    if len(choices) == 2:
        return said + f"expected `{choices[0]}` or `{choices[1]}`"
    return said + "expected one of " + ", ".join(f"`{c}`" for c in choices)


# --- reading a dataclass --------------------------------------------------------------------------------------------

_made: dict[Any, Schema] = {}
_making = threading.local()
_lock = threading.Lock()

ANY = Schema("any")


def of(kind: Any) -> Schema:
    """The schema of ``kind``: a dataclass, or any annotation an input field may carry. None is ``any``."""
    if kind is None:
        return ANY
    if dataclasses.is_dataclass(kind) and isinstance(kind, type):
        with _lock:
            known = _made.get(kind)
        if known is not None:
            return known
        made = _object(kind)
        with _lock:
            _made.setdefault(kind, made)
        return made
    optional, schema = _shape(kind, "the input")
    if optional:
        raise SchemaError(f"granite: the input is {kind!r}: an input is a dataclass, not an optional")
    return schema


def _object(kind: type) -> Schema:
    making = getattr(_making, "kinds", None)
    if making is None:
        making = _making.kinds = set()
    if kind in making:
        raise SchemaError(f"granite: {kind.__name__} holds itself, which an input cannot")
    making.add(kind)
    try:
        hints = typing.get_type_hints(kind, include_extras=True)
        fields = []
        seen: set[str] = set()
        for f in dataclasses.fields(kind):
            if not f.init:
                continue
            where = f"{kind.__name__}.{f.name}"
            optional, schema = _shape(hints.get(f.name, Any), where)
            meta = f.metadata.get("granite", {})
            name = meta.get("name") or camel(f.name)
            if name in seen:
                raise SchemaError(f"granite: {where} reads `{name}`, which another field reads too")
            seen.add(name)
            if f.default is not dataclasses.MISSING:
                default: Any = (lambda d=f.default: d)
            elif f.default_factory is not dataclasses.MISSING:
                default = f.default_factory
            else:
                default = None
            fields.append(Field(name, f.name, not optional, schema, meta.get("note"), default))
        return Schema("object", fields=tuple(fields), type=kind)
    finally:
        making.discard(kind)


def _shape(hint: Any, where: str) -> tuple[bool, Schema]:
    """Whether ``hint`` is optional, and the schema of what it holds."""
    origin = typing.get_origin(hint)
    if origin is Annotated:
        base, *extras = typing.get_args(hint)
        widths = [e for e in extras if isinstance(e, _Width)]
        optional, schema = _shape(base, where)
        if widths:
            if schema.kind != "integer":
                raise SchemaError(f"granite: {where} gives a width to something that is not an int")
            schema = Schema("integer", width=widths[-1])
        return optional, schema
    if origin is Union or origin is types.UnionType:
        members = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(members) == 1 and len(members) < len(typing.get_args(hint)):
            optional, schema = _shape(members[0], where)
            return True, schema
        raise SchemaError(f"granite: {where} is {hint!r}: an input field holds one type, or one type | None")
    if hint is Any or hint is object or hint is dict or origin is dict:
        return False, ANY
    if hint is str:
        return False, Schema("string")
    if hint is bool:
        return False, Schema("boolean")
    if hint is int:
        return False, Schema("integer")
    if hint is float:
        return False, Schema("number")
    if origin is Literal:
        choices = typing.get_args(hint)
        if not all(type(c) is str for c in choices):
            raise SchemaError(f"granite: {where} is {hint!r}: a choice is a Literal of strings")
        return False, Schema("choice", choices=tuple(choices))
    if hint is list or origin is list:
        args = typing.get_args(hint)
        if not args:
            return False, Schema("array", items=ANY)
        optional, items = _shape(args[0], where)
        if optional:
            raise SchemaError(f"granite: {where} is a list of optionals, which an input cannot hold")
        return False, Schema("array", items=items)
    if dataclasses.is_dataclass(hint) and isinstance(hint, type):
        return False, of(hint)
    raise SchemaError(f"granite: {where} is {hint!r}, which an input cannot take: use str, bool, int, float, "
                      "a Literal of strings, list, a dataclass or Any, or one of those | None")
