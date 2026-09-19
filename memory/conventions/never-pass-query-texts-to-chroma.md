---
memdex:
  id: mem_558925bf
  title: Never pass query_texts to Chroma
  category: conventions
  description: Every call passes explicit embeddings= or query_embeddings=.
  importance: 0.6
  source: MEMORY.md > Decisions > Never pass query_texts to Chroma
---
# Never pass query_texts to Chroma

Every call passes explicit `embeddings=` or `query_embeddings=`. If a collection
has no embedding function and you pass `query_texts` or bare `documents`, Chroma
falls back to its default ONNX model and silently downloads roughly 80MB. A unit
test greps `store.py` to make sure the string never reappears.
