"""``granite check``'s validator against GraniteRust's: every case in goldens/check said as Rust's ``granite check``
says it, and a census that fails when a schema uses a keyword the validator does not read. GraniteC's
test_validate.c."""

import os
import unittest
from pathlib import Path

from granite import agentjson
from granite.jsonschema import KEYWORDS, Schemas, validate

ROOT = Path(__file__).parent.parent
GOLDENS = Path(__file__).parent / "goldens" / "check"


def registry():
    schemas = Schemas()
    schemas.add_folder(str(ROOT / "Schema"))
    schemas.add_folder(str(ROOT / "containers" / "schemas"))
    return schemas


def as_rust_says(schemas, name, instance):
    """What the Rust command prints to stderr for one case, and its exit code."""
    schema = schemas.find(name)
    if schema is None:
        return 2, f"granite: {name}: no such schema\n"
    problems = validate(schemas, schema, instance)
    if not problems:
        return 0, f"valid: instance.json against {name}\n"
    said = "".join(f"{p}\n" for p in problems)
    return 1, said + f"granite: instance.json does not keep {name}: {len(problems)} problems\n"


class JSONSchemaTests(unittest.TestCase):
    def test_every_case_is_said_as_graniterusts_check_says_it(self):
        cases = [agentjson.read(line) for line in (GOLDENS / "cases.jsonl").read_bytes().splitlines() if line]
        expected = [agentjson.read(line) for line in (GOLDENS / "rust.expected").read_bytes().splitlines() if line]
        self.assertEqual(len(cases), 59)
        self.assertEqual(len(cases), len(expected))
        schemas = registry()
        mismatches = []
        for index, (case, want) in enumerate(zip(cases, expected), 1):
            code, said = as_rust_says(schemas, case["schema"], case["instance"])
            if (code, said) != (want["code"], want["stderr"]):
                mismatches.append(f"case {index} against {case['schema']}:\n  Python ({code}): {said}"
                                  f"  Rust ({want['code']}): {want['stderr']}")
        self.assertEqual(mismatches, [], "\n".join(mismatches[:4]))

    def test_every_keyword_a_schema_uses_is_one_the_validator_reads(self):
        unknown = []

        def census(node, where):
            if type(node) is list:
                for item in node:
                    census(item, where)
                return
            if type(node) is not dict:
                return
            for key, value in node.items():
                if key not in KEYWORDS:
                    unknown.append(f"{where} uses `{key}`")
                if key in ("properties", "$defs") and type(value) is dict:
                    for child in value.values():
                        census(child, where)
                elif key not in ("enum", "const", "examples"):
                    census(value, where)

        for folder in (ROOT / "Schema", ROOT / "containers" / "schemas"):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".json"):
                    census(agentjson.read((folder / name).read_bytes()), name)
        self.assertEqual(unknown, [])


if __name__ == "__main__":
    unittest.main()
