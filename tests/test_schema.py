"""What an input takes, read off a dataclass; granite-answer/1; and the wire against Rust's 79 lines. GraniteC's
test_answer.c and test_wire.c, ported."""

import binascii
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import granite.schema as schema
from granite import agentjson, wire
from granite.answer import Answer, Make, problems, resolved, stamped
from granite.error import Cancelled, Fault, Refusal
from granite.schema import SchemaError, field, u32

GOLDENS = Path(__file__).parent / "goldens"


@dataclass
class Card:
    name: str = field(note="who the card is for")
    count: int | None = None
    mood: Literal["calm", "loud", "sly"] = "calm"
    tags: list[str] | None = None
    directory: str | None = None


@dataclass
class Parrot:
    text: str = field(note="what to say")
    notes: u32 | None = None
    ask_model: bool | None = None
    delay: float | None = None
    crash: bool | None = None
    fail: bool | None = None
    fault: bool | None = None
    directory: str | None = None


@dataclass
class Inner:
    at: float


@dataclass
class Outer:
    inner: Inner
    many: list[Inner]
    anything: Any = None


class SchemaTests(unittest.TestCase):
    def test_a_schema_is_written_as_rust_writes_its_data_form(self):
        card = schema.of(Card)
        self.assertEqual(agentjson.write(card.value()),
                         b'{"object":[{"name":"name","required":true,"schema":"string"},'
                         b'{"name":"count","required":false,"schema":"integer"},'
                         b'{"name":"mood","required":true,"schema":{"choice":["calm","loud","sly"]}},'
                         b'{"name":"tags","required":false,"schema":{"array":"string"}},'
                         b'{"name":"directory","required":false,"schema":"string"}]}')
        self.assertEqual(schema.of(None).value(), "any")
        self.assertEqual(card.notes(), {"name": "who the card is for"})
        self.assertTrue(card.takes_directory)

    def test_the_parrots_input_reads_as_rusts_parrot_input(self):
        describe = agentjson.read((GOLDENS / "parrot.describe").read_bytes())
        entry = describe["entries"][0]
        self.assertEqual(schema.of(Parrot).value(), entry["schema"])
        self.assertEqual(schema.of(Parrot).notes(), entry["fields"])

    def test_a_model_is_shown_json_schema_without_the_hosts_folder(self):
        self.assertEqual(agentjson.write(schema.of(Card).json_schema(True)),
                         b'{"properties":{"count":{"type":"integer"},"mood":{"enum":["calm","loud","sly"],'
                         b'"type":"string"},"name":{"description":"who the card is for","type":"string"},'
                         b'"tags":{"items":{"type":"string"},"type":"array"}},"required":["name","mood"],'
                         b'"type":"object"}')

    def test_a_value_that_does_not_read_is_said_in_serdes_words(self):
        rows = [
            ('{"name":"ada","mood":"calm"}', None),
            ('{"name":"ada","mood":"calm","count":null,"extra":[1]}', None),
            ('{"mood":"calm"}', "missing field `name`"),
            ('{"name":"ada"}', "missing field `mood`"),
            ('{"name":5,"mood":"calm"}', "invalid type: integer `5`, expected a string"),
            ('{"name":"ada","mood":"grim"}', "unknown variant `grim`, expected one of `calm`, `loud`, `sly`"),
            ('{"name":"ada","mood":"calm","count":1.5}', "invalid type: floating point `1.5`, expected i64"),
            ('{"name":"ada","mood":"calm","count":9223372036854775808}',
             "invalid value: integer `9223372036854775808`, expected i64"),
            ('{"name":"ada","mood":"calm","count":1e20}',
             "invalid type: floating point `100000000000000000000.0`, expected i64"),
            ('{"name":"ada","mood":"calm","tags":["a",2]}', "invalid type: integer `2`, expected a string"),
            ('{"name":"ada","mood":"calm","tags":"a"}', 'invalid type: string "a", expected a sequence'),
            ('[1]', "invalid type: sequence, expected struct Card"),
            ('{"count":true}', "invalid type: boolean `true`, expected i64"),
            ('{"name":"a\\n\\u0001","mood":7}', "invalid type: integer `7`, expected string or map"),
            ('{"mood":7,"name":"a"}', "invalid type: integer `7`, expected string or map"),
        ]
        card = schema.of(Card)
        for text, said in rows:
            with self.subTest(text):
                self.assertEqual(card.check(agentjson.read(text)), said)
        parrot = schema.of(Parrot)
        self.assertEqual(parrot.check({"text": "hi", "notes": -1}), "invalid value: integer `-1`, expected u32")
        self.assertEqual(parrot.check({"text": "hi", "delay": "2"}), 'invalid type: string "2", expected f64')
        self.assertEqual(parrot.check({"text": ["x\ty"]}), "invalid type: sequence, expected a string")
        self.assertEqual(schema.unexpected("a\x01\"\\"), 'string "a\\u{1}\\"\\\\"')
        self.assertEqual(schema.unexpected(-0.0), "floating point `-0.0`")
        self.assertEqual(schema.unexpected(0.000015), "floating point `0.000015`")
        self.assertEqual(schema.unexpected(12.5), "floating point `12.5`")

    def test_an_input_decodes_into_its_dataclass(self):
        parrot = schema.of(Parrot).decode({"text": "hi", "askModel": True, "delay": 2})
        self.assertEqual((parrot.text, parrot.ask_model, parrot.delay, parrot.notes), ("hi", True, 2.0, None))
        self.assertIs(type(parrot.delay), float)
        outer = schema.of(Outer).decode({"inner": {"at": 1}, "many": [{"at": 2.5}], "anything": {"x": [1]}})
        self.assertEqual(outer, Outer(Inner(1.0), [Inner(2.5)], {"x": [1]}))
        self.assertEqual(schema.of(Outer).check({"inner": 3, "many": []}),
                         "invalid type: integer `3`, expected struct Inner")
        with self.assertRaises(ValueError) as said:
            schema.of(Outer).decode({"inner": {"at": 1}})
        self.assertEqual(str(said.exception), "missing field `many`")

    def test_a_type_an_input_cannot_take_is_said_with_its_fix(self):
        @dataclass
        class Odd:
            pick: set

        @dataclass
        class Two:
            either: int | str

        @dataclass
        class Twice:
            ask_model: bool
            askModel: bool

        for kind, part in ((Odd, "Odd.pick is <class 'set'>"), (Two, "an input field holds one type"),
                           (Twice, "reads `askModel`, which another field reads too")):
            with self.subTest(kind.__name__), self.assertRaises(SchemaError) as said:
                schema.of(kind)
            self.assertIn(part, str(said.exception))


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.folder = os.path.realpath(tempfile.mkdtemp(prefix="granitepython-answer-"))
        self.makes = [Make("card", "a card", ["cards/"])]

    def joined(self, answer):
        return " | ".join(problems(answer, "maker", self.makes, self.folder))

    def test_a_conforming_answer_has_no_problems_and_is_stamped(self):
        answer = Answer(card="ada")
        answer.path("cards/ada.card.json").made("card", "ada", "cards/ada.card.json").note("1 card made")
        self.assertEqual(self.joined(answer), "")
        self.assertEqual(agentjson.write(stamped(answer, "maker")),
                         b'{"agent":{"word":"maker"},"card":"ada","expected":[],"format":"granite-answer/1",'
                         b'"made":[{"kind":"card","name":"ada","path":"cards/ada.card.json","state":"written"}],'
                         b'"notes":["1 card made"],"path":"cards/ada.card.json","paths":["cards/ada.card.json"],'
                         b'"payloads":[],"problems":[]}')
        self.assertEqual(agentjson.write(Answer()),
                         b'{"expected":[],"made":[],"path":null,"paths":[],"payloads":[],"problems":[]}')

    def test_each_way_to_break_the_standard_is_said_as_swift_says_it(self):
        os.mkdir(os.path.join(self.folder, "cards"))
        with open(os.path.join(self.folder, "cards", "ada.card.json"), "w") as payload:
            payload.write('{"schema":"other-card/1"}')
        answer = Answer()
        answer.add_path("cards/ada.card.json")
        answer["path"] = "cards/elsewhere.card.json"
        answer.made("chart", "ada", "cards/ada.card.json").made("card", "", None).made("card", "bob", None, "rewritten")
        answer.made("card", "cy", "cards/ada.card.json", "torn")
        answer.expected("poster", "x").problem("Partly Done", "x")
        answer.payload("cards/ada.card.json", "test-card/1").payload("cards/none.json", "test-card/1")
        answer["format"] = "granite-answer/1"
        answer["notes"] = "one"
        self.assertEqual(self.joined(answer),
                         "`format` is the runner's to say: an answer does not encode it"
                         " | `path` cards/elsewhere.card.json is not one of `paths`"
                         " | `made[0]` is a `chart`, and maker makes `card`"
                         " | `made[1]` needs a `kind` and a `name`"
                         " | `made[2]` was written, so it names its `path`"
                         " | `made[3].state` is one of `written`, `rewritten`, `unchanged`, `removed`"
                         " | `expected[0]` is a `poster`, and maker makes `card`"
                         " | `problems[0].kind` is one lowercase word"
                         " | `payloads[0]` says `test-card/1`, and the file's own `schema` is `other-card/1`"
                         " | `payloads[1]` cards/none.json is not one of `paths`"
                         " | `notes` is a list of sentences")
        self.assertEqual(self.joined({"made": 1}),
                         "`paths` is missing: an answer names every file it wrote, `[]` when none | `made` is not a list")
        self.assertEqual(" | ".join(problems({"paths": [], "made": [{"kind": "x", "name": "y"}]}, "plain")),
                         "`made[0]` is a `x`, and plain declares nothing it makes (`makes`)"
                         " | `made[0]` was written, so it names its `path`")

    def test_paths_are_compared_as_files(self):
        full = resolved(os.path.join(self.folder, "cards", "a.json"))
        self.assertEqual(resolved("cards/a.json", self.folder), full)
        self.assertEqual(resolved("./cards/../cards/a.json", self.folder), full)
        if sys.platform == "darwin":
            self.assertEqual(resolved("/tmp/x/y"), "/private/tmp/x/y")
        self.assertEqual(resolved("/../.."), "/")


class WireTests(unittest.TestCase):
    def test_every_line_reads_and_is_written_back_as_rust_does(self):
        lines = [binascii.unhexlify(l.strip()) for l in (GOLDENS / "wire" / "corpus.hex").read_text().splitlines()]
        expected = (GOLDENS / "wire" / "rust.expected").read_text().splitlines()
        self.assertEqual(len(lines), 79)
        self.assertEqual(len(expected), 79)
        mismatches = []
        for text, want in zip(lines, expected):
            try:
                got = "ok " + binascii.hexlify(wire.line(wire.read(text))).decode()
            except wire.WireError as error:
                got = "err " + binascii.hexlify(error.said.encode()).decode()
            if got != want:
                mismatches.append(f"{text!r}\n  got  {got}\n  want {want}")
        self.assertEqual(mismatches, [], "\n".join(mismatches[:5]))

    def test_messages_are_written_as_one_sorted_line(self):
        self.assertEqual(wire.line(wire.hello(42)),
                         b'{"hello":{"answer":"granite-answer/1","pid":42,"wire":"granite-agent/1"}}\n')
        self.assertEqual(wire.line(wire.failed(Cancelled())), b'{"failed":"cancelled"}\n')
        self.assertEqual(wire.line(wire.failed(Refusal("call", "say what to plate"))),
                         b'{"failed":"say what to plate","refused":{"kind":"call","reasons":["say what to plate"]}}\n')
        self.assertEqual(wire.line(wire.failed(Fault("it broke"))), b'{"failed":"it broke"}\n')
        ask = wire.ModelAsk(3, "object", {"type": "object"}, "Count.", "How many?", 16, 5.0)
        self.assertEqual(wire.line(ask), b'{"model":{"id":3,"kind":"object","maxTokens":16,"schema":{"type":"object"},'
                                         b'"seconds":5,"system":"Count.","user":"How many?"}}\n')
        read = wire.read(b'{"failed":"x","refused":{"kind":"call","reasons":[]}}\n')
        self.assertEqual((read.error.kind, read.error.said, read.error.reasons), ("call", "x", []))


if __name__ == "__main__":
    unittest.main()
