---
memdex:
  id: mem_6e1d5c73
  title: No-op runs stay quiet
  category: development
  description: An unchanged repo gets one "Nothing to sync" line and no history row.
  importance: 0.7
  source: development session — no-op messaging
---
# No-op runs stay quiet

Teams run `memdex run` and `memdex refresh` habitually, most often with nothing
to do. Both commands now answer with a single line — "Nothing to sync …" /
"Nothing to refresh …" — instead of reprinting a full report, and neither writes
an audit row when it changed nothing.

The audit rule matters beyond tidiness: the dashboard's token chart plots history
rows, so recording no-ops would fill it with flat lines and make real
optimizations harder to see. A run counts as work when it wrote a file or
generated an embedding; anything else is polling.

`compact` still prints its analysis every time (that is what it is for) but also
records nothing when it changes nothing.
