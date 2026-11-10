# syntax=docker/dockerfile:1
# The check image: Python 3.12, git and GranitePython's granite command, so a container can install any Python agent
# repository with GranitePython's installer (pyproject.toml → zipapp → --describe → installation.json) and drive its
# runner as a host would. containers/check.sh builds and runs it with Apple's `container`.
FROM docker.io/library/python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The commit GranitePython was built from: .git is not in the build context, so check.sh passes it.
ARG GRANITE_COMMIT=unknown
WORKDIR /src/GranitePython
COPY . .
RUN GRANITE_COMMIT="$GRANITE_COMMIT" python3 scripts/build-cli.py --out /usr/local/bin/granite --python /usr/local/bin/python3 \
 && mkdir -p /usr/local/share/granitepython/schemas \
 && cp Schema/*.schema.json containers/schemas/*.schema.json /usr/local/share/granitepython/schemas/

WORKDIR /scratch
ENTRYPOINT ["granite"]
