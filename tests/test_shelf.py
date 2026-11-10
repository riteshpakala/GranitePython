"""The host's records: where code comes from, installation.json, and a shelf read the way GraniteRust's AgentShelf
reads one. GraniteC's test_shelf.c."""

import os
import unittest

from granite import agentjson
from granite.error import Refusal
from granite.host import files, shelf
from granite.host.installation import FORMAT, Installation
from granite.host.remote import ENDED, REFUSED, RemoteError
from granite.host.source import Source


class SourceTests(unittest.TestCase):
    def test_a_source_is_a_repository_at_a_revision_or_a_folder(self):
        rows = [
            ("https://github.com/rao/ComicAgent.git", Source(url="https://github.com/rao/ComicAgent.git")),
            (" https://github.com/rao/ComicAgent.git@f9bdda7 ",
             Source(url="https://github.com/rao/ComicAgent.git", revision="f9bdda7")),
            ("git@github.com:rao/ComicAgent.git", Source(url="git@github.com:rao/ComicAgent.git")),
            ("/abs/agent/../agent", Source(path="/abs/agent")),
            ("./agent", Source(path="/work/here/agent")),
            ("../agent", Source(path="/work/agent")),
            ("~/agents/comic", Source(path=f"{os.environ['HOME']}/agents/comic")),
        ]
        for said, source in rows:
            with self.subTest(said):
                self.assertEqual(Source.parse(said, "/work/here"), source)
        pinned = Source.parse("https://x/y.git@abc", "/")
        self.assertEqual((pinned.newest().text, pinned.text), ("https://x/y.git", "https://x/y.git@abc"))


def install(folder, name, word, data):
    inside = files.join(folder, name)
    files.mkdirs(inside)
    runner = files.join(inside, "agent")
    files.write(runner, data)
    size, modified = files.stamp_of(runner)
    record = Installation(name=name, source=Source(path="/src/agent"), executable="agent",
                          sha256=shelf.sha256_file(runner), size=size, built_at="2026-11-09T12:00:00Z",
                          entries=[{"kind": "package", "word": word, "summary": "s", "module": "M", "type": "M.T"}],
                          granite="0.1.0@abc", modified=modified)
    files.write(files.join(inside, "installation.json"), agentjson.write_pretty(record.json()))


class ShelfTests(unittest.TestCase):
    def test_an_installation_record_reads_back_as_it_was_written(self):
        record = Installation(name="comic", source=Source(url="https://x/comic.git"), executable="agent", sha256="00",
                              size=7, built_at="2026-11-09T12:00:00Z", revision="f9bdda7")
        json = record.json()
        self.assertEqual(agentjson.write(json),
                         b'{"builtAt":"2026-11-09T12:00:00Z","entries":[],"executable":"agent",'
                         b'"format":"granite-agent-installation/1","name":"comic","revision":"f9bdda7",'
                         b'"sha256":"00","size":7,"source":{"url":"https://x/comic.git"}}')
        back = Installation.read(json)
        self.assertEqual((back.source.url, back.modified, back.size), ("https://x/comic.git", None, 7))
        del json["sha256"]
        with self.assertRaises(ValueError) as said:
            Installation.read(json)
        self.assertEqual(str(said.exception), "missing field `sha256`")
        rows = [
            ({**record.json(), "size": -1}, "invalid value: integer `-1`, expected u64"),
            ({**record.json(), "size": "7"}, 'invalid type: string "7", expected u64'),
            ({**record.json(), "source": {"url": 3}}, "data did not match any variant of untagged enum AgentSource"),
            ({**record.json(), "name": 5, "size": "x"}, "invalid type: integer `5`, expected a string"),
            ({**record.json(), "modified": "x"}, 'invalid type: string "x", expected f64'),
            ([], "invalid type: sequence, expected struct AgentInstallation"),
        ]
        for value, sentence in rows:
            with self.subTest(sentence), self.assertRaises(ValueError) as said:
                Installation.read(value)
            self.assertEqual(str(said.exception), sentence)

    def test_a_shelf_reads_its_installations_and_says_what_does_not_read(self):
        folder = files.temporary_folder("shelf")
        install(folder, "Comic", "comic", b"runner one")
        install(folder, "plate", "plate", b"runner two")
        for name in ("broken", "later", ".Comic.new", "empty"):
            files.mkdirs(files.join(folder, name))
        files.write(files.join(folder, "broken/installation.json"), b'{"format":')
        files.write(files.join(folder, "later/installation.json"),
                    b'{"format":"granite-agent-installation/9","name":"later","source":{"path":"/x"},'
                    b'"executable":"agent","sha256":"0","size":0,"builtAt":"x","entries":[]}')
        read = shelf.read_all(folder)
        self.assertEqual([i.name for i in read.installations], ["Comic", "plate"])
        self.assertEqual(read.problems,
                         ["broken: its installation.json does not read: EOF while parsing a value at line 1 column 10",
                          "later: granite-agent-installation/9 is not an installation this host reads"])
        by_name, by_word = shelf.find(folder, "comic"), shelf.find(folder, "PLATE")
        self.assertEqual((by_name.name, by_word.name), ("Comic", "plate"))
        self.assertIsNone(shelf.find(folder, "nobody"))
        self.assertEqual(by_name.granite, "0.1.0@abc")

        self.assertIsNone(shelf.verify(folder, by_name))
        runner = shelf.executable_of(folder, by_name)
        files.write(runner, b"runner one, changed")
        changed = shelf.verify(folder, by_name)
        self.assertEqual(str(changed), f"comic's executable changed since it was installed ({runner}); install it again")
        stamp = shelf.stamp(folder)
        self.assertIn("Comic@", stamp)
        self.assertIn("plate@", stamp)
        self.assertTrue(files.remove_tree(folder))
        self.assertFalse(files.exists(folder))
        self.assertEqual(FORMAT, "granite-agent-installation/1")

    def test_each_remote_error_is_said_as_rust_says_it(self):
        ended = RemoteError(ENDED, "parrot", status="signal 6 (SIGABRT)", log="this line must not reach the wire")
        self.assertEqual(str(ended),
                         "parrot's process ended (signal 6 (SIGABRT)) without answering: this line must not reach the "
                         "wire")
        refused = RemoteError(REFUSED, "parrot", message="told to fail",
                              refusal=Refusal("call", "told to fail: leave out `fail`"))
        self.assertEqual(str(refused), "parrot refused (call): told to fail")


if __name__ == "__main__":
    unittest.main()
