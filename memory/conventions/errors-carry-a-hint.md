---
memdex:
  id: mem_63f87cb8
  title: Errors carry a hint
  category: conventions
  description: Every user-facing error has a message and a hint saying what to do next.
  importance: 0.5
  source: MEMORY.md > Conventions > Errors carry a hint
---
# Errors carry a hint

Every user-facing error has a message and a hint saying what to do next. The CLI
renders them as a red ✗ plus a dim hint and exits 1; anything else is an internal
bug and is only re-raised when `MEMDEX_DEBUG=1` is set.
