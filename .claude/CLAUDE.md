# Working on Memdex

Memdex is a local-first CLI that turns sprawling AI-assistant memory into a small
index plus a local ChromaDB vector store. Python 3.10+, managed with `uv`.

## Use the project's own memory

This repository is indexed by Memdex. **Read `MEMORY.md` first** — it is a small
index of titles, one-line descriptions and paths, not a knowledge dump. Open only
the memory files relevant to the task at hand.

To find something by meaning rather than by title:

```bash
memdex search "why is the pipeline split in two?" --top-k 3
```

Then read the files it returns. Do not load the whole `memory/` tree into context.

After changing something a memory describes, update that memory file and run
`memdex run` so the index and vectors follow. After pulling other people's
commits, run `memdex refresh` — it flags memories whose cited code changed
or disappeared, and re-indexes pulled memory edits. Never hand-edit `MEMORY.md` — it is
generated, and the marker at the top says so.

## Commands

```bash
uv sync --all-extras     # set up
uv run pytest            # the whole suite, offline, ~5s
uv run pytest tests/unit # fast unit tests only
uv run ruff check .      # lint (line length 100)
uv run memdex --help     # run the CLI from the working tree
./setup.sh --dev         # sync + test + lint in one go
```

## Conventions that matter here

- **Never let a change alter the meaning of a user's memory.** The compressor
  removes conversational filler and repeated paragraphs, nothing else. It never
  paraphrases, never reflows, and never touches the inside of a code fence.
- **Plan and apply stay separate.** `pipeline.build_plan` is pure — it reads
  files and computes everything without writing or opening the vector store.
  `pipeline.apply_plan` does all side effects. This is what makes `--dry-run`
  trustworthy; do not sneak a write into the plan phase.
- **`store.py` is the only module that may import `chromadb`.** Keep it that way
  so an upstream API change stays a one-file fix.
- **Never pass `query_texts` or bare `documents` to Chroma.** Always pass
  explicit `embeddings=` / `query_embeddings=`, otherwise Chroma silently
  downloads an embedding model of its own. A test enforces this.
- **Re-running must stay a no-op.** Generated files carry no timestamps, output
  paths are pure functions of category and title, compression is a fixed point,
  and the diff compares rendered bytes against disk. If you add a field to a
  generated file, make sure it is deterministic.
- **Readonly sources are never written to.** `layout.diff_ops` asserts that every
  planned file operation lands under a managed source or the output paths.
- **Failures degrade, they do not abort.** A missing embedding model falls back
  to the hash embedder with a warning; any LLM failure keeps the deterministic
  result and still exits 0. The markdown files are the product.
- **Tests must not touch the network.** `tests/conftest.py` makes the downloading
  embedder raise, and every fixture uses `embedding.provider: hash`.

## Layout

`src/memdex/` — `cli` (commands) · `project` (repo detection) · `config` ·
`discovery` · `parsing` · `optimize/` · `identity` (stable IDs) · `layout` ·
`indexdoc` · `embed/` · `store` (ChromaDB) · `audit` · `backup` · `pipeline` ·
`bootstrap` · `tokens` · `output` (all rendering) · `ui/` (stdlib dashboard).

Rendering lives in `output.py` only; pipeline stages take a progress callback so
they stay testable without a console.
