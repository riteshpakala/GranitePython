"""JSON read and written as serde_json reads and writes it: every case in tests/goldens/json/corpus.hex against what
serde_json 1.0.154 made of it (serde.expected)."""

import pathlib
import unittest

from granite import agentjson
from granite.agentjson import JSONError

GOLDENS = pathlib.Path(__file__).resolve().parent / "goldens" / "json"


class SerdeCorpus(unittest.TestCase):
    def test_every_case_reads_and_writes_as_serde_json_does(self):
        cases = (GOLDENS / "corpus.hex").read_text().split("\n")[:-1]
        expected = (GOLDENS / "serde.expected").read_text().split("\n")[:-1]
        self.assertEqual(len(cases), len(expected))
        wrong = []
        for number, (case, want) in enumerate(zip(cases, expected), 1):
            text = bytes.fromhex(case)
            kind, *rest = want.split(" ")
            try:
                value = agentjson.read(text)
            except JSONError as error:
                said = error.sentence
                if kind != "err" or said != bytes.fromhex(rest[0]).decode():
                    wrong.append(f"{number} {text[:50]!r}: said {said!r}, serde {want[:12]} "
                                 f"{bytes.fromhex(rest[0]).decode() if kind == 'err' else ''!r}")
                continue
            if kind != "ok":
                wrong.append(f"{number} {text[:50]!r}: read it, serde said {bytes.fromhex(rest[0]).decode()!r}")
                continue
            compact, pretty = bytes.fromhex(rest[0]), bytes.fromhex(rest[1])
            if agentjson.write(value) != compact:
                wrong.append(f"{number} {text[:50]!r}: wrote {agentjson.write(value)[:80]!r}, serde {compact[:80]!r}")
            elif agentjson.write_pretty(value) != pretty:
                wrong.append(f"{number} {text[:50]!r}: indented differently")
        self.assertEqual(wrong[:12], [], f"{len(wrong)} of {len(cases)} cases differ from serde_json")


class Values(unittest.TestCase):
    def test_a_whole_double_is_an_integer_as_swift_writes_it(self):
        for value, written in ((300.0, b"300"), (300.5, b"300.5"), (-2.0, b"-2"), (0.0, b"0"),
                               (9007199254740992.0, b"9007199254740992.0"), (1e20, b"1e+20"), (0.25, b"0.25")):
            self.assertEqual(agentjson.write(agentjson.number(value)), written)

    def test_a_boolean_is_never_a_number(self):
        self.assertEqual(agentjson.write({"a": True, "b": 1, "c": 1.0}), b'{"a":true,"b":1,"c":1.0}')
        self.assertFalse(agentjson.equal(True, 1))
        self.assertTrue(agentjson.equal({"x": [1, 2.0]}, {"x": [1.0, 2]}))
        self.assertFalse(agentjson.equal({"x": 1}, {"x": 1, "y": None}))

    def test_what_is_not_json_is_refused(self):
        with self.assertRaises(TypeError):
            agentjson.write({1: "a"})
        with self.assertRaises(TypeError):
            agentjson.write(object())
        self.assertEqual(agentjson.write(float("nan")), b"null")
        self.assertEqual(agentjson.write("lone \udc80"), '"lone �"'.encode())


if __name__ == "__main__":
    unittest.main()
