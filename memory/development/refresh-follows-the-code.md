---
memdex:
  id: mem_4b8e1a9d
  title: Refresh follows the code
  category: development
  description: memdex refresh diffs the worktree against a git baseline and flags memories citing changed code.
  importance: 0.8
  source: development session — refresh command
---
# Refresh follows the code

`memdex refresh` keeps memory honest in repositories several teams commit to. It
stores a baseline commit in `.memdex/metadata/refresh.json`, diffs it against the
working tree (pulled commits and uncommitted edits both count), and matches
changed paths and basenames against memory text. A separate scan flags memories
citing path-like tokens that no longer exist anywhere in the repo.

Deterministically it only flags — rewriting what a memory says is never an
automatic edit. With `--llm`, fixable candidates (managed, Memdex-generated
files; never readonly rules) are shown the current code and the model answers
keep / update / obsolete per memory; updates are applied after a backup, and the
run ends with a pipeline pass so everything re-embeds. The internal pipeline
pass records no `run` audit row — refresh logs exactly one `refresh` event.

Proven the day it was built: the first refresh on a real work repository flagged
two assistant rule files citing a generated graph JSON that did not exist there.
(Write such examples without the literal path — a bare path in memory is itself
a citation, and refresh would flag this very file for it.)
