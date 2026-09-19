---
memdex:
  id: mem_cebfd639
  title: Command modes
  category: development
  description: run, compact and index are the same stages with different masks, so the three commands cannot drift apart.
  importance: 0.45
  source: MEMORY.md > Architecture > Command modes
---
# Command modes

`run`, `compact` and `index` are the same stages with different masks, so the
three commands cannot drift apart. `compact` plans identically to `run` and only
differs in presentation. `index` regenerates MEMORY.md and refreshes embeddings
without restructuring anything — it passes `index_only=True` to `layout.diff_ops`
so it can never conclude that the memory files it deliberately did not plan are
obsolete.
