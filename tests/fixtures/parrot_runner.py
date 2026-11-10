"""The parrot's runner, from the source tree: what an installed parrot's zipapp runs. Test-only."""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_here, "..", "..", "src"), os.path.join(_here, "agent-repo", "src")]

import granite  # noqa: E402
from granite_conformance import roster  # noqa: E402

if __name__ == "__main__":
    granite.serve(roster)
