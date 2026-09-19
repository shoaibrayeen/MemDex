---
memdex:
  id: mem_a63407aa
  title: Readonly sources are never written to
  category: conventions
  description: layout.diff_ops asserts that every planned file operation lands under a managed source or one of the output paths.
  importance: 0.6
  source: MEMORY.md > Conventions > Readonly sources are never written to
---
# Readonly sources are never written to

`layout.diff_ops` asserts that every planned file operation lands under a managed
source or one of the output paths. `.claude/` and `.cursor/` are instructions to
a tool rather than notes the user owns, so Memdex indexes them and leaves them
alone.
