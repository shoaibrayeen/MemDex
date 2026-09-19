---
memdex:
  id: mem_ba698f09
  title: Compression must never change meaning
  category: conventions
  description: This is the rule the whole optimizer is built around.
  importance: 0.6
  source: MEMORY.md > Conventions > Compression must never change meaning
---
# Compression must never change meaning

This is the rule the whole optimizer is built around. Compression removes
conversational filler lines, collapses blank-line runs, and drops paragraphs
repeated verbatim. It never paraphrases, never reflows, and never touches the
inside of a code fence.

Compression is also a fixed point: `compress(compress(x)) == compress(x)`, which
is enforced by a unit test. Without that property a second `memdex run` could not
be a genuine no-op.
