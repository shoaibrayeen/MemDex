---
memdex:
  id: mem_8a19e0b9
  title: Vector store boundary
  category: architecture
  description: store.py is the only module that imports chromadb.
  importance: 0.75
  source: MEMORY.md > Architecture > Vector store boundary
---
# Vector store boundary

`store.py` is the only module that imports `chromadb`. Keeping every Chroma call
behind that boundary means an upstream API change is a one-file fix, and it makes
the privacy promise checkable in one place.

The memories collection uses cosine space, which must be set at creation time and
cannot be changed later. Audit rows live in a separate `memdex_audit` collection
with a constant one-dimensional vector, because they need durability, not
similarity search.
