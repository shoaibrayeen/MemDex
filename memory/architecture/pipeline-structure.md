---
memdex:
  id: mem_7f4121dc
  title: Pipeline structure
  category: architecture
  description: The pipeline is deliberately split in two halves.
  importance: 0.75
  source: MEMORY.md > Architecture > Pipeline structure
---
# Pipeline structure

The pipeline is deliberately split in two halves. `pipeline.build_plan` is pure:
it reads files and computes the whole result — the new memory tree, the index,
the file diff, the token numbers — without writing anything or opening the
vector store. `pipeline.apply_plan` performs every side effect, in a fixed
order: back up, write files, save the registry, embed, sync ChromaDB, record the
activity log entry.

This split is why `--dry-run` is trustworthy rather than a best-effort preview.
Never move a write into the plan phase.
