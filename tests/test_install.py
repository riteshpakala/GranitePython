"""A Python agent repository installed, called, installed again and removed, as GraniteRust's installer test does
with its hello agent; and each way an install is refused, in its sentence. GraniteC's test_install.c."""

import os
import shutil
import subprocess
import unittest

from granite import agentjson, version
from granite.host import files, install, shelf
from granite.host.install import (BUILD, CLASH, DEPENDENCIES, FETCH, NO_AGENT, NOT_A_PACKAGE, NOT_GRANITE,
                                  NOT_INSTALLED, PYTHON, InstallError)
from granite.host.process import Process
from granite.host.source import Source
from tests.support import FIXTURES

REPOSITORY = os.path.realpath(FIXTURES / "agent-repo")
PYPROJECT = (FIXTURES / "agent-repo" / "pyproject.toml").read_text()


def copy_repository(folder, name, *, broken=False, pyproject=None):
    shutil.copytree(REPOSITORY, folder, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    text = pyproject if pyproject is not None else PYPROJECT.replace('name = "parrot-python"', f'name = "{name}"')
    with open(os.path.join(folder, "pyproject.toml"), "w") as out:
        out.write(text)
    if broken:
        with open(os.path.join(folder, "src", "granite_conformance", "__init__.py"), "w") as out:
            out.write("this is not Python:\n")


def said(call):
    try:
        call()
        return "(installed)"
    except InstallError as error:
        return str(error)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.made = []

    def tearDown(self):
        for folder in self.made:
            files.remove_tree(folder)

    def folder(self, what):
        made = os.path.realpath(files.temporary_folder(what))
        self.made.append(made)
        return made

    def test_a_repository_is_installed_called_reinstalled_and_removed(self):
        folder = self.folder("install-shelf")
        stages = []
        record = install.install(folder, Source.parse(REPOSITORY, "/"), lambda stage, line: stages.append(stage))
        self.assertEqual(stages, ["reading the package", "building parrot-python", "asking parrot-python what it carries",
                                  "installing parrot-python", "installed parrot-python: parrot"])
        self.assertEqual((record.name, record.executable, record.source.path, record.word),
                         ("parrot-python", "agent", REPOSITORY, "parrot"))
        self.assertTrue(record.granite.startswith(version.VERSION))
        self.assertEqual(len(record.revision or ""), 40)

        found = shelf.find(folder, "PARROT")
        self.assertIsNotNone(found)
        self.assertIsNone(shelf.verify(folder, found))
        executable = shelf.executable_of(folder, found)
        with open(executable, "rb") as runner:
            self.assertTrue(runner.readline().endswith(b" -I\n"))
        output, _ = Process(executable).perform("parrot", '{"text":"hi"}')
        self.assertEqual(agentjson.read(output)["said"], "hi")

        # Again: the same repository replaces itself whole, with the same bytes.
        again = install.install(folder, Source.parse(REPOSITORY, "/"))
        self.assertEqual(again.sha256, record.sha256)
        self.assertEqual(len(shelf.read_all(folder).installations), 1)
        self.assertEqual(os.listdir(os.path.join(folder, ".builds")), [])

        # Another repository that carries `parrot` is refused.
        other = self.folder("install-other")
        copy_repository(other, "parrot-twin")
        self.assertEqual(said(lambda: install.install(folder, Source.parse(other, "/"))),
                         f"an agent called `parrot` is already installed, from {REPOSITORY}")

        removed = install.remove(folder, "parrot-python")
        self.assertEqual(removed.name, "parrot-python")
        self.assertEqual(said(lambda: install.remove(folder, "parrot-python")),
                         "no agent installed here is called parrot-python")
        self.assertEqual(said(lambda: install.update(folder, "parrot-python")),
                         "no agent installed here is called parrot-python")

    def test_each_way_an_install_is_refused_is_said(self):
        folder = self.folder("install-refused")
        empty = self.folder("install-empty")

        def refused(path, kind):
            try:
                install.install(folder, Source.parse(path, "/"))
            except InstallError as error:
                self.assertEqual(error.kind, kind, str(error))
                return str(error)
            self.fail(f"{path} installed")

        self.assertEqual(refused(empty, NOT_A_PACKAGE), f"{empty} is not a Python agent repository: it has no "
                                                        "pyproject.toml")
        for file, who in (("Cargo.toml", "GraniteRust's granite installs Rust agents"),
                          ("granite-agent.json", "GraniteC's granite installs C agents")):
            files.write(os.path.join(empty, file), b"{}")
            self.assertEqual(refused(empty, NOT_A_PACKAGE),
                             f"{empty} is not a Python agent repository: it has no pyproject.toml (it has a {file}: "
                             f"{who})")
            os.unlink(os.path.join(empty, file))
        files.write(os.path.join(empty, "pyproject.toml"), b'[project]\nname = "x"\n')
        self.assertEqual(refused(empty, NOT_A_PACKAGE),
                         f"{empty} is not a Python agent repository: its pyproject.toml has no [tool.granite]")
        files.write(os.path.join(empty, "pyproject.toml"), b'[tool.granite]\nname = "x"\nagent = "roster"\n')
        self.assertEqual(refused(empty, NOT_A_PACKAGE),
                         f"{empty} is not a Python agent repository: `agent` is not module:attribute: roster")
        files.write(os.path.join(empty, "pyproject.toml"), b"[tool.granite\n")
        self.assertIn("its pyproject.toml does not read: ", refused(empty, NOT_A_PACKAGE))

        broken = self.folder("install-broken")
        copy_repository(broken, "broken-parrot", broken=True)
        text = refused(broken, BUILD)
        self.assertTrue(text.startswith("broken-parrot did not build (the whole log is at "), text)
        self.assertIn("granite_conformance/__init__.py", text)

        newer = self.folder("install-newer")
        copy_repository(newer, "newer-parrot", pyproject=PYPROJECT.replace('name = "parrot-python"',
                                                                          'name = "newer-parrot"')
                        .replace('granite = "0.1.0"', 'granite = "9.0"'))
        self.assertEqual(refused(newer, NOT_GRANITE), "newer-parrot does not build against GranitePython: it needs "
                                                      f"GranitePython 9.0, and this is {version.VERSION}")

        needy = self.folder("install-needy")
        copy_repository(needy, "needy-parrot", pyproject=PYPROJECT.replace('name = "parrot-python"', 'name = "needy"')
                        .replace("dependencies = []", 'dependencies = ["numpy>=2", "pillow"]'))
        self.assertEqual(refused(needy, DEPENDENCIES),
                         "needy depends on numpy>=2, pillow, which a Python runner does not bundle yet: it carries the "
                         "standard library and GranitePython only")

        old = self.folder("install-old")
        copy_repository(old, "old", pyproject=PYPROJECT.replace('name = "parrot-python"', 'name = "old"')
                        .replace('requires-python = ">=3.11"', 'requires-python = "<3.9"'))
        self.assertIn("old does not run on this Python: it needs Python <3.9, and this is ", refused(old, PYTHON))

        lost = self.folder("install-lost")
        copy_repository(lost, "lost", pyproject=PYPROJECT.replace('name = "parrot-python"', 'name = "lost"')
                        .replace('"granite_conformance:roster"', '"granite_conformance:nobody"'))
        text = refused(lost, NO_AGENT)
        self.assertTrue(text.startswith("lost (agent's process ended (exit 1) without answering: "), text)
        self.assertIn("AttributeError", text)

        self.assertEqual(shelf.read_all(folder).installations, [])

    def test_a_repository_is_fetched_from_git_and_its_revision_kept(self):
        folder = self.folder("install-git-shelf")
        origin = self.folder("install-git-origin")
        copy_repository(origin, "parrot-git")
        for arguments in (["init", "--quiet"], ["add", "."],
                          ["-c", "user.name=granite", "-c", "user.email=granite@example.invalid", "commit", "--quiet",
                           "-m", "parrot"]):
            subprocess.run(["git", *arguments], cwd=origin, check=True, capture_output=True)
        url = f"file://{origin}"
        stages = []
        record = install.install(folder, Source.parse(url, "/"), lambda stage, line: stages.append(stage))
        self.assertEqual(stages[0], f"fetching {url}")
        self.assertEqual((len(record.revision or ""), record.source.url), (40, url))
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True).stdout.strip()
        pinned = install.install(folder, Source.parse(f"{url}@{head}", "/"))
        self.assertEqual((pinned.source.revision, pinned.revision), (head, head))
        self.assertEqual(pinned.sha256, record.sha256)
        with self.assertRaises(InstallError) as missing:
            install.install(folder, Source.parse("file:///nowhere/at/all.git", "/"))
        self.assertEqual(missing.exception.kind, FETCH)
        self.assertEqual(CLASH, "clash")
        self.assertEqual(NOT_INSTALLED, "not-installed")

    def test_requires_python_is_read_as_a_specifier(self):
        fits = install.python_fits
        self.assertTrue(fits(">=3.11", (3, 12, 8)))
        self.assertFalse(fits(">=3.13", (3, 12, 8)))
        self.assertTrue(fits(">=3.11,<4", (3, 12, 8)))
        self.assertTrue(fits("~=3.11", (3, 12, 8)))
        self.assertFalse(fits("~=3.11.2", (3, 12, 8)))
        self.assertTrue(fits("==3.12.*", (3, 12, 8)))
        self.assertFalse(fits("==3.11.*", (3, 12, 8)))
        self.assertTrue(fits("weird", (3, 12, 8)))


if __name__ == "__main__":
    unittest.main()
