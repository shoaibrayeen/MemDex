# Memdex — local-first memory optimizer for AI coding assistants.
#
# The image carries the CLI; the project it works on is whatever you mount at
# /workspace. Nothing is stored in the image itself — the vector store lives in
# the project's own .memdex/, exactly as it does outside Docker.
#
#   docker build -t memdex .
#   docker run --rm -v "$PWD:/workspace" memdex init --here --yes
#   docker run --rm -v "$PWD:/workspace" -v memdex-cache:/root/.cache memdex run
#   docker run --rm -v "$PWD:/workspace" memdex search "why did we choose X?"
#   docker run --rm -p 7644:7644 -v "$PWD:/workspace" memdex ui --host 0.0.0.0
#
# The named cache volume keeps the one-time ~80MB embedding-model download from
# repeating on every container. Skip it and Memdex still works — it falls back
# to the offline hash embedder with a warning.

FROM python:3.12-slim AS build

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist


FROM python:3.12-slim

LABEL org.opencontainers.image.title="Memdex" \
      org.opencontainers.image.description="Local-first memory optimizer for AI coding assistants" \
      org.opencontainers.image.source="https://github.com/memdex/memdex" \
      org.opencontainers.image.version="1.0.5-beta"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEMDEX_IN_DOCKER=1

# git powers `memdex refresh`; a bind-mounted repo belongs to the host user, so
# it must be marked safe inside this single-purpose container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

RUN --mount=from=build,source=/dist,target=/dist \
    pip install "$(ls /dist/*.whl)[tokens]"

# The project being indexed mounts here; every memdex command runs against it.
WORKDIR /workspace

# The dashboard, when started with: memdex ui --host 0.0.0.0
EXPOSE 7644

ENTRYPOINT ["memdex"]
CMD ["--help"]
