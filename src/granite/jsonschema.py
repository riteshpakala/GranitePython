"""The JSON Schema behind ``granite check``: the keywords the standard's schemas use, each said as GraniteRust's
``granite check`` (jsonschema-rs) says it, in the same order. GraniteC's validate.c.

A problem reads ``<sentence> at <JSON pointer>``. Keywords are read in sorted order; ``properties`` and
``additionalProperties`` visit members in sorted order; ``oneOf`` and ``if`` ask quietly whether a subschema keeps the
instance. A keyword not listed in ``KEYWORDS`` is not read, and the census test fails when a schema uses one.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any

from granite import agentjson

KEYWORDS = (
    "$ref", "$defs", "$id", "type", "properties", "required", "items", "additionalProperties", "oneOf", "allOf", "if",
    "then", "else", "enum", "const", "pattern", "minimum",
    # annotations: they say nothing to a validator
    "$schema", "title", "description", "examples", "format",
)


class Schemas:
    """Schema documents by name and by ``$id``, for ``$ref`` to reach."""

    def __init__(self) -> None:
        self._documents: dict[str, Any] = {}

    def add(self, name: str, document: Any) -> None:
        if isinstance(document, dict) and type(document.get("$id")) is str:
            self._documents[document["$id"]] = document
        self._documents[name] = document

    def add_folder(self, folder: str) -> None:
        """Every ``.json`` in ``folder`` that reads and has an ``$id``. Raises ``OSError`` when it cannot be read."""
        for name in os.listdir(folder):
            if len(name) < 5 or not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(folder, name), "rb") as opened:
                    document = agentjson.read(opened.read())
            except (OSError, agentjson.JSONError):
                continue
            if isinstance(document, dict) and type(document.get("$id")) is str:
                self.add(name, document)

    def find(self, name: str) -> Any:
        return self._documents.get(name)


def _shown(value: Any) -> str:
    return agentjson.write(value).decode("utf-8")


def _type_of(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is list:
        return "array"
    return "object"


def _is_type(value: Any, wanted: str) -> bool:
    actual = _type_of(value)
    if wanted == actual:
        return True
    if wanted == "number":
        return actual == "integer"
    if wanted == "integer" and actual == "number":
        return math.isfinite(value) and value == math.trunc(value)
    return False


def _segment(name: str) -> str:
    return "/" + name.replace("~", "~0").replace("/", "~1")


def _follow(document: Any, pointer: str) -> Any:
    """The node ``pointer`` (``#/…`` without the ``#``) names in ``document``; ``_MISSING`` when none."""
    at = document
    if not pointer:
        return at
    if not pointer.startswith("/"):
        return _MISSING
    for token in pointer[1:].split("/"):
        if token == "":
            continue
        segment = token.replace("~1", "/").replace("~0", "~")
        if type(at) is list:
            if not segment.isdigit() or int(segment) >= len(at):
                return _MISSING
            at = at[int(segment)]
        elif type(at) is dict and segment in at:
            at = at[segment]
        else:
            return _MISSING
    return at


_MISSING = object()


class _Validation:
    def __init__(self, schemas: Schemas) -> None:
        self.schemas = schemas

    def keeps(self, schema: Any, document: Any, instance: Any) -> bool:
        quiet: list[str] = []
        self.validate(schema, document, instance, "", quiet)
        return not quiet

    def validate(self, schema: Any, document: Any, instance: Any, path: str, found: list[str]) -> None:
        def say(sentence: str) -> None:
            found.append(f"{sentence} at {path}")

        if type(schema) is bool:
            if not schema:
                say(f"False schema does not allow {_shown(instance)}")
            return
        if type(schema) is not dict:
            return
        for keyword in sorted(schema):
            value = schema[keyword]
            if keyword == "$ref":
                self.ref(value, document, instance, path, found)
            elif keyword == "type":
                self.type(value, instance, say)
            elif keyword == "properties":
                if type(instance) is dict and type(value) is dict:
                    for name in sorted(value):
                        if name in instance:
                            self.validate(value[name], document, instance[name], path + _segment(name), found)
            elif keyword == "additionalProperties":
                self.additional(schema, value, document, instance, path, found, say)
            elif keyword == "required":
                if type(instance) is dict and type(value) is list:
                    for name in value:
                        if type(name) is str and name not in instance:
                            say(f"{_shown(name)} is a required property")
            elif keyword == "items":
                if type(instance) is list:
                    for index, item in enumerate(instance):
                        self.validate(value, document, item, f"{path}/{index}", found)
            elif keyword == "allOf":
                for each in value if type(value) is list else []:
                    self.validate(each, document, instance, path, found)
            elif keyword == "oneOf":
                matched = sum(1 for each in (value if type(value) is list else []) if self.keeps(each, document, instance))
                if matched != 1:
                    if matched:
                        say(f"{_shown(instance)} is valid under more than one of the schemas listed in the 'oneOf' "
                            "keyword")
                    else:
                        say(f"{_shown(instance)} is not valid under any of the schemas listed in the 'oneOf' keyword")
            elif keyword == "if":
                if self.keeps(value, document, instance):
                    if "then" in schema:
                        self.validate(schema["then"], document, instance, path, found)
                elif "else" in schema:
                    self.validate(schema["else"], document, instance, path, found)
            elif keyword == "enum":
                if not any(agentjson.equal(option, instance) for option in (value if type(value) is list else [])):
                    say(f"{_shown(instance)} is not one of {_shown(value)}")
            elif keyword == "const":
                if not agentjson.equal(value, instance):
                    say(f"{_shown(value)} was expected")
            elif keyword == "pattern":
                if type(instance) is str and type(value) is str:
                    try:
                        matched_pattern = re.search(value, instance) is not None
                    except re.error:
                        matched_pattern = True
                    if not matched_pattern:
                        say(f"{_shown(instance)} does not match {_shown(value)}")
            elif keyword == "minimum":
                if agentjson.is_number(instance) and agentjson.is_number(value) and instance < value:
                    say(f"{_shown(instance)} is less than the minimum of {_shown(value)}")
            # $defs, $id, then, else and the annotations say nothing by themselves.

    def type(self, types: Any, instance: Any, say: Any) -> None:
        if type(types) is str:
            if not _is_type(instance, types):
                say(f'{_shown(instance)} is not of type "{types}"')
            return
        names = [t for t in types if type(t) is str] if type(types) is list else []
        if any(_is_type(instance, t) for t in names):
            return
        listed = ", ".join(f'"{t}"' for t in sorted(names))
        say(f"{_shown(instance)} is not of types {listed}")

    def additional(self, schema: dict, additional: Any, document: Any, instance: Any, path: str, found: list[str],
                   say: Any) -> None:
        if type(instance) is not dict:
            return
        properties = schema.get("properties")
        properties = properties if type(properties) is dict else {}
        unexpected = []
        for name in sorted(instance):
            if name in properties:
                continue
            if type(additional) is bool:
                if not additional:
                    unexpected.append(f"'{name}'")
                continue
            self.validate(additional, document, instance[name], path + _segment(name), found)
        if unexpected:
            said = "was" if len(unexpected) == 1 else "were"
            say(f"Additional properties are not allowed ({', '.join(unexpected)} {said} unexpected)")

    def ref(self, ref: Any, document: Any, instance: Any, path: str, found: list[str]) -> None:
        if type(ref) is not str:
            return
        target_document, fragment = document, ref
        if not ref.startswith("#"):
            uri, hash, rest = ref.partition("#")
            fragment = "#" + rest if hash else "#"
            target_document = self.schemas.find(uri)
        target = _follow(target_document, fragment[1:]) if target_document is not None else _MISSING
        if target is _MISSING:
            found.append(f"unresolvable reference {ref} at {path}")
        else:
            self.validate(target, target_document, instance, path, found)


def validate(schemas: Schemas, schema: Any, instance: Any) -> list[str]:
    """Every way ``instance`` breaks ``schema``, one ``<sentence> at <pointer>`` each; ``[]`` when it keeps it."""
    found: list[str] = []
    _Validation(schemas).validate(schema, schema, instance, "", found)
    return found
