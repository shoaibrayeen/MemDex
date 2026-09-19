---
memdex:
  id: mem_da852a14
  title: Stable IDs through an adoption ladder
  category: decisions
  description: Identity is adopted through a ladder of increasingly weak signals rather than derived from the file path, so an…
  importance: 0.65
  source: MEMORY.md > Decisions > Stable IDs through an adoption ladder
---
# Stable IDs through an adoption ladder

Identity is adopted through a ladder of increasingly weak signals rather than
derived from the file path, so an assistant that cites `mem_a8f31c` still finds
it after the memory moves or gets edited:

1. A valid `memdex.id` in the frontmatter of a generated file — survives in-place
   edits. A second claimant of an ID already taken is re-minted with a warning.
2. An exact content hash match — survives a move with no frontmatter.
3. A title and category match — survives a body rewritten in place.
4. A fresh ID.

Moving, retitling and rewriting a memory all at once produces a new ID. That is
an accepted, documented limitation.
