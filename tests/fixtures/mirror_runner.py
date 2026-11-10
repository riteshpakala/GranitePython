"""``mirror``: answers with its input, as the runner read it and writes it back. What interop holds to serde: every
value of the JSON corpus through a Python runner's reader and writer, against what serde_json makes of the same text.
Test-only."""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "..", "src"))

import granite  # noqa: E402


class Mirror(granite.AgentPackage):
    word = "mirror"
    summary = "answers with what it was given."
    module = "granite_interop"
    type = "granite_interop.mirror"

    def run(self, input, context):
        context.deliver(granite.Answer(mirrored=input.get("value") if isinstance(input, dict) else None))


if __name__ == "__main__":
    granite.serve([Mirror])
