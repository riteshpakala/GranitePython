#!/bin/sh
# Schema/ is a verbatim copy of Granite's; this says when it has fallen behind (a Granite checkout as $1).
set -eu
cd "$(dirname "$0")/.."
granite=${1:-../Granite}
[ -d "$granite/Schema" ] || { echo "drift-check: no Granite checkout at $granite"; exit 1; }
for f in GraniteAgent.md granite-agent-answer.schema.json granite-agent-entry.schema.json \
         granite-agent-installation.schema.json granite-agent-wire.schema.json; do
  cmp -s "Schema/$f" "$granite/Schema/$f" || { echo "drift-check: Schema/$f differs from $granite/Schema"; exit 1; }
done
echo "drift-check: Schema/ matches $granite/Schema"
