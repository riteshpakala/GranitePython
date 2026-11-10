"""The granite command from the source tree, for the CLI tests. Test-only."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from granite.cli import entry  # noqa: E402

if __name__ == "__main__":
    entry()
