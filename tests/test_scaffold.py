"""What every other test stands on: the standard's schemas and the golden corpora are here, whole."""

import pathlib
import unittest

import granite

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Scaffold(unittest.TestCase):
    def test_the_standard_and_its_schemas_are_here(self):
        for name in ("GraniteAgent.md", "granite-agent-answer.schema.json", "granite-agent-entry.schema.json",
                     "granite-agent-installation.schema.json", "granite-agent-wire.schema.json"):
            self.assertTrue((ROOT / "Schema" / name).stat().st_size > 1000, name)

    def test_every_corpus_answers_each_of_its_cases(self):
        for folder, cases, expected in (("json", "corpus.hex", "serde.expected"), ("wire", "corpus.hex", "rust.expected"),
                                        ("check", "cases.jsonl", "rust.expected"), ("cli", "cases.jsonl", "rust.expected")):
            base = ROOT / "tests" / "goldens" / folder
            asked = (base / cases).read_bytes().split(b"\n")
            said = (base / expected).read_bytes().split(b"\n")
            self.assertEqual(len(asked), len(said), folder)
            self.assertGreater(len(asked), 20, folder)

    def test_the_version_is_said_once(self):
        self.assertRegex(granite.__version__, r"^\d+\.\d+\.\d+$")
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn(f'version = "{granite.__version__}"', pyproject)


if __name__ == "__main__":
    unittest.main()
