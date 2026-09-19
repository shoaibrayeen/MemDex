---
memdex:
  id: mem_b2027942
  title: Memory units
  category: architecture
  description: A memory unit is one topic, not one chapter.
  importance: 0.75
  source: MEMORY.md > Architecture > Memory units
---
# Memory units

A memory unit is one topic, not one chapter. Sections split down to their leaves
so that retrieval returns "Qdrant Selection" rather than all of "Decisions"; the
chapter heading survives as the breadcrumb and drives categorization. Sections
smaller than `min_unit_tokens` fold back into their parent, heading and all, so
nothing is ever lost.

Splitting only ever happens at markdown heading boundaries. A paragraph is never
cut in half, and an oversized section with no internal headings is kept whole and
reported instead.
