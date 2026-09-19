---
memdex:
  id: mem_ff37ea24
  title: Running the tests
  category: development
  description: '`uv sync --all-extras`'
  importance: 0.55
  source: MEMORY.md > Development > Running the tests
---
# Running the tests

```bash
uv sync --all-extras
uv run pytest            # whole suite, about five seconds
uv run pytest tests/unit # fast unit tests only
uv run ruff check .      # line length 100
./setup.sh --dev         # sync, test and lint in one command
```
