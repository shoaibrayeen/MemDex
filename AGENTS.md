# AGENTS.md

Instructions for coding agents (Codex and any agent that reads `AGENTS.md`)
working in the Memdex repository.

## What this project is

Memdex is a local-first CLI that turns sprawling AI-assistant memory
(`MEMORY.md`, `memory/`, `.claude/`, `.cursor/`) into a small index plus a local
ChromaDB vector store, so an assistant loads an index and retrieves only the
memories it needs. Python 3.10+, managed with `uv`.

## Use the project's own memory before reading code

This repository is indexed by Memdex:

1. Read `MEMORY.md` — a small index of titles, one-line descriptions and paths.
2. Open only the memory files relevant to your task.
3. To search by meaning: `memdex search "<question>" --top-k 3`.
4. After pulling new commits: `memdex refresh` (flags memories whose cited
   code changed; `--llm` lets the model update them).

Do not load the whole `memory/` tree. `MEMORY.md` is generated — edit files under
`memory/` and run `memdex run` to regenerate the index and vectors.

## Setup and checks

```bash
uv sync --all-extras       # install (uv fetches Python 3.12)
uv run pytest              # full suite — offline, ~5 seconds
uv run ruff check .        # lint; line length 100
uv run memdex --help       # run the CLI from the working tree
./setup.sh --dev           # all of the above in one command
```

Every change must leave `uv run pytest` and `uv run ruff check .` passing.
Add tests for new behavior in `tests/unit/` (pure logic) or `tests/integration/`
(commands end to end, driven through Typer's `CliRunner`).

## Invariants — do not break these

| Invariant | Why |
| --- | --- |
| Compression never changes meaning | It removes conversational filler and repeated paragraphs only. No paraphrasing, no reflowing, nothing inside a code fence. |
| `build_plan` is pure | It must not write files or open the vector store. `--dry-run` is only trustworthy because of this; side effects live in `apply_plan`. |
| Only `store.py` imports `chromadb` | Keeps an upstream API change to one file. |
| Never pass `query_texts` to Chroma | Always pass explicit `embeddings=` / `query_embeddings=`; otherwise Chroma downloads a model of its own. A test asserts this. |
| Re-running changes nothing | No timestamps in generated files; paths are pure functions of category and title; compression is a fixed point; the diff compares rendered bytes against disk. |
| Readonly sources are never written | `layout.diff_ops` asserts every file operation lands under a managed source or the output paths. |
| Failures degrade, never abort | A missing embedding model falls back to the hash embedder; any LLM failure keeps the deterministic result and exits 0. |
| Tests never touch the network | `tests/conftest.py` makes the downloading embedder raise; fixtures use `embedding.provider: hash`. |
| Memory is never destroyed silently | Every applying command backs up what it touches to `.memdex/backups/<timestamp>/` first. Obsolete memories are reported, never deleted. |

## Where things live

```
src/memdex/
  cli.py        command surface (thin: resolve, run a pipeline, render)
  project.py    is this a code repo, where is its root
  config.py     .memdex/config.yaml
  discovery.py  finding memory files (configured locations only)
  parsing.py    frontmatter + fence-aware heading tree
  optimize/     structure, dedupe, categorize, compress, deterministic, llm
  identity.py   hashes, slugs, stable IDs, the registry
  layout.py     planning the memory tree on disk
  indexdoc.py   rendering MEMORY.md
  embed/        local (ONNX), hash (offline), ollama
  store.py      ChromaDB — the only module that imports it
  audit.py      the activity log
  backup.py     backups and restore
  pipeline.py   plan (pure) then apply (side effects)
  bootstrap.py  generating a first memory from code
  tokens.py     tiktoken, or a clearly labelled estimate
  output.py     all terminal rendering
  ui/           the local dashboard (standard library only)
```

## Style

- Line length 100; ruff rules `E, F, I, UP, B, W`.
- Type hints on public functions; `from __future__ import annotations` at the top.
- Comments explain constraints and reasons, not what the next line does.
- User-facing errors carry a message *and* a hint saying what to do next.
