---
memdex:
  id: mem_16c3e02b
  title: Re-running is a no-op by construction
  category: conventions
  description: Generated files carry no timestamps — times live in the registry and backup manifests instead.
  importance: 0.5
  source: MEMORY.md > Conventions > Re-running is a no-op by construction
---
# Re-running is a no-op by construction

Generated files carry no timestamps — times live in the registry and backup
manifests instead. Output paths are pure functions of the final category and
title. Index entries have a total ordering. The diff compares rendered bytes
against what is on disk and emits nothing when they match.

The result is that a second run writes nothing, takes no backup, and re-embeds
nothing. If you add a field to a generated file, make sure it is deterministic.
