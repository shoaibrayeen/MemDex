---
memdex:
  id: mem_ec21a369
  title: Tests never touch the network
  category: development
  description: tests/conftest.py patches the downloading embedder so that it raises, which turns any stray network call into a loud…
  importance: 0.6
  source: MEMORY.md > Development > Tests never touch the network
---
# Tests never touch the network

`tests/conftest.py` patches the downloading embedder so that it raises, which
turns any stray network call into a loud failure rather than a slow test. Every
project fixture uses `embedding.provider: hash`, the pure-standard-library
embedder, so the suite runs identically on a laptop and in an air-gapped CI box.

Integration tests drive the real CLI through Typer's `CliRunner` against a copy
of `examples/demo-project`, and assert on real files, real ChromaDB collections
and recursive tree hashes rather than on mocks.
