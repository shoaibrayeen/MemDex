---
memdex:
  id: mem_ca06cc26
  title: Python instead of Go
  category: decisions
  description: The original specification asked for Go and for a local ChromaDB.
  importance: 0.65
  source: MEMORY.md > Decisions > Python instead of Go
---
# Python instead of Go

The original specification asked for Go and for a local ChromaDB. Those two
requirements conflict: ChromaDB's embedded persistent client and its bundled
local embedding model are Python libraries. A Go build would have to run and
supervise a separate `chroma` server process and find another route to local
embeddings — more moving parts, a weaker offline story, harder cross-platform
distribution — in exchange for a single binary.

Python keeps the database embedded and the whole tool genuinely offline. `uv`
makes installation a one-liner and fetches the right interpreter, so the
practical difference for users is small.
