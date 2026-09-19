---
memdex:
  id: mem_4cff6b47
  title: Console output wraps
  category: architecture
  description: Rich wraps output to the terminal width, so asserting on a long phrase from result.output fails intermittently…
  importance: 0.65
  source: MEMORY.md > Development > Console output wraps
---
# Console output wraps

Rich wraps output to the terminal width, so asserting on a long phrase from
`result.output` fails intermittently depending on path length. Both integration
test modules define a `flat()` helper that collapses whitespace before matching.
