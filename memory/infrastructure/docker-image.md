---
memdex:
  id: mem_7d3e8b2c
  title: Docker image
  category: infrastructure
  description: The image carries only the CLI; the target project mounts at /workspace.
  importance: 0.7
  source: development session — dockerization
---
# Docker image

Two-stage build (`python:3.12-slim`): the first stage builds the wheel, the
second installs it with the `tokens` extra. The entrypoint is `memdex`; the
project being indexed mounts at `/workspace`, and its `.memdex/` lives in that
mount — nothing project-specific is ever baked into the image.

Operational notes proven by test:

- Mount `memdex-cache:/root/.cache` so the one-time ~80MB embedding-model
  download survives across containers.
- The dashboard needs `ui --host 0.0.0.0` inside a container (127.0.0.1 is
  unreachable from the host); compose publishes it on `127.0.0.1:7644` only.
- A `.memdex/` store built natively on macOS reads identically inside the Linux
  container — same vectors, same scores — so host and container can be mixed.
- To reach an Ollama on the host from a container, set
  `llm.base_url: http://host.docker.internal:11434/v1` (it counts as local, so
  no remote-endpoint warning).
- `docker compose config` must stay valid; compose passes `OPENAI_API_KEY`
  through from the shell and never stores it.
