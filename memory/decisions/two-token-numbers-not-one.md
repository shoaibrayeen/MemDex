---
memdex:
  id: mem_6212e9a6
  title: Two token numbers, not one
  category: decisions
  description: Memdex reports context cost and content size separately, because they measure different things and conflating them…
  importance: 0.65
  source: MEMORY.md > Decisions > Two token numbers, not one
---
# Two token numbers, not one

Memdex reports context cost and content size separately, because they measure
different things and conflating them would overstate the result.

Context cost is what an assistant loads unconditionally: all of the memory
before, only the index after. That is the product's actual claim. Content size is
the stored memory itself, which shrinks only by however much duplication and
filler there genuinely was. Reporting a single blended number would be a
marketing figure, not a measurement.
