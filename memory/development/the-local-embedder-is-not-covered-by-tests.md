---
memdex:
  id: mem_ae29a22e
  title: The local embedder is not covered by tests
  category: development
  description: The default local provider downloads an ONNX MiniLM model, so the suite stubs it out and exercises the hash provider…
  importance: 0.55
  source: MEMORY.md > Development > The local embedder is not covered by tests
---
# The local embedder is not covered by tests

The default `local` provider downloads an ONNX MiniLM model, so the suite stubs
it out and exercises the `hash` provider instead. That means the default path has
to be checked by hand after touching `embed/` or `store.py`:

```bash
cd examples/demo-project && memdex init --here && memdex run && memdex search "why chroma"
```
