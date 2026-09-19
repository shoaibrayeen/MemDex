---
memdex:
  id: mem_56e0f0ef
  title: Managed sources beat readonly ones
  category: decisions
  description: When the same memory appears in both a managed source and a readonly one, the managed copy wins and the readonly twin…
  importance: 0.65
  source: MEMORY.md > Decisions > Managed sources beat readonly ones
---
# Managed sources beat readonly ones

When the same memory appears in both a managed source and a readonly one, the
managed copy wins and the readonly twin is shadowed — left on disk untouched, but
kept out of the index and the vector store. Only managed memory can be curated
and kept current, so promoting the readonly copy would strand the canonical
version.
