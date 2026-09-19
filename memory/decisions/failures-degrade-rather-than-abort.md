---
memdex:
  id: mem_2622598e
  title: Failures degrade rather than abort
  category: decisions
  description: The markdown files are the product; the vectors are an index over them, and an index can always be rebuilt.
  importance: 0.75
  source: MEMORY.md > Decisions > Failures degrade rather than abort
---
# Failures degrade rather than abort

The markdown files are the product; the vectors are an index over them, and an
index can always be rebuilt. So a missing embedding model falls back to the hash
embedder with a warning, and any LLM failure at all — unreachable, slow,
malformed, out of contract — keeps the deterministic result and still exits 0.
