---
memdex:
  id: mem_2c7f9e4b
  title: Assistant-driven bootstrap needs no LLM
  category: decisions
  description: With no memory and no LLM, Memdex seeds a template the user's own coding assistant fills.
  importance: 0.85
  source: development session — assistant template
---
# Assistant-driven bootstrap needs no LLM

When a project has no memory and no LLM is configured, `memdex run` and
`memdex bootstrap` write a MEMORY.md template addressed to the coding assistant
already working in the repository (Claude Code, Cursor, Codex): fill each
section with real memories, then run `memdex run`. The assistant is the LLM —
Memdex never needs credentials of its own for this path.

Mechanics that make the round trip clean:

- Template instructions live in HTML comments; `compress` strips comments
  outside code fences, so scaffolding never becomes memory.
- Unfilled sections hold the fixed placeholder `_(none yet)_`, which is also
  stripped; a unit left empty after compression is dropped entirely.
- `memdex run` on a still-unfilled template (detected by the
  `<!-- memdex:template` marker plus zero live units) explains what to do
  instead of reporting an empty index.
- The template is written with a `seed` backup manifest and audit event, so
  `memdex restore` can unwind it like everything else.
