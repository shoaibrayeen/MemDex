---
memdex:
  id: mem_180ced15
  title: Rendering
  category: architecture
  description: All terminal rendering lives in output.py.
  importance: 0.65
  source: MEMORY.md > Architecture > Rendering
---
# Rendering

All terminal rendering lives in `output.py`. Pipeline stages take a progress
callback instead of printing, so they stay testable without a console. The web
dashboard in `ui/` is a read-only view over the same data and adds no
dependencies — it is standard-library HTTP plus one self-contained HTML file.
