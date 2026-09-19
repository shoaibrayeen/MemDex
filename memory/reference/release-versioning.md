---
memdex:
  id: mem_1f2a8d6e
  title: Release versioning
  category: reference
  description: Every change bumps the patch and writes a changelog line; minor and major need --confirm.
  importance: 0.7
  source: development session — versioning policy
---
# Release versioning

**Every change ships a patch bump with its changelog entry**, in the same commit:

```bash
python scripts/bump_version.py --tag fix \
    --note "What changed, in one line" \
    --why "Why it changed, and what it fixes for the reader."
```

Minor and major releases are the maintainer's decision, so the script refuses
them without `--confirm` — an agent must never take that call on its own. It
also refuses to bump without `--note`, which is what keeps `changelog.html` from
falling behind the code.

Two spellings come from one source of truth: the display version (`1.0.1-beta`,
shown by `memdex --version` and the Docker image label) and the PEP 440 form
(`1.0.1b0`, which packaging tools require). The script writes both into
`src/memdex/__init__.py`, `pyproject.toml`, the Dockerfile and the README;
`tests/unit/test_cli_layout.py::TestVersioning` asserts they stay one release,
and `TestVersionBumpPolicy` asserts the refusals actually refuse.
