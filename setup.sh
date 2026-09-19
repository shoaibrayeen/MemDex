#!/usr/bin/env bash
#
# Memdex one-shot setup.
#
#   ./setup.sh                 install the memdex CLI (with dashboard)
#   ./setup.sh --no-ui         install, dashboard disabled in new projects
#   ./setup.sh --offline       install and default new projects to the offline embedder
#   ./setup.sh --prefetch      also download the embedding model now (~80MB, one time)
#   ./setup.sh --dev           set up a development environment and run the tests
#   ./setup.sh --uninstall     remove the installed CLI
#
# Safe to re-run: every step is idempotent. Nothing here touches project memory.

set -euo pipefail

WITH_UI=1
OFFLINE=0
PREFETCH=0
DEV=0
UNINSTALL=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; OFF=$'\033[0m'

ok()   { printf '%s✓%s %s\n' "$GREEN" "$OFF" "$1"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$OFF" "$1"; }
die()  { printf '%s✗%s %s\n' "$RED" "$OFF" "$1" >&2; exit 1; }
step() { printf '\n%s%s%s\n' "$BOLD" "$1" "$OFF"; }
dim()  { printf '%s%s%s\n' "$DIM" "$1" "$OFF"; }

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

for arg in "$@"; do
    case "$arg" in
        --with-ui)   WITH_UI=1 ;;
        --no-ui)     WITH_UI=0 ;;
        --offline)   OFFLINE=1 ;;
        --prefetch|--prefetch-model) PREFETCH=1 ;;
        --dev)       DEV=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)   usage ;;
        *)           die "Unknown option: $arg (try --help)" ;;
    esac
done

printf '\n%sMemdex setup%s\n' "$BOLD" "$OFF"
dim "Local-first memory optimizer for AI coding assistants."

# ---------------------------------------------------------------------------
# 1. uv
# ---------------------------------------------------------------------------
step "1. Checking for uv"
if command -v uv >/dev/null 2>&1; then
    ok "uv $(uv --version | awk '{print $2}') is installed"
else
    warn "uv is not installed. It manages the Python version Memdex needs (3.10+)."
    printf '  Install it now from https://astral.sh/uv? [y/N] '
    read -r reply </dev/tty || reply=""
    case "$reply" in
        [yY]*)
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # The installer puts uv in one of these; pick it up without a new shell.
            for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
                [ -x "$candidate/uv" ] && export PATH="$candidate:$PATH"
            done
            command -v uv >/dev/null 2>&1 || die "uv still isn't on PATH — open a new shell and re-run."
            ok "uv installed"
            ;;
        *)
            die "uv is required. Install it, then re-run this script."
            ;;
    esac
fi

if [ "$UNINSTALL" -eq 1 ]; then
    step "Uninstalling"
    uv tool uninstall memdex >/dev/null 2>&1 && ok "Removed the memdex CLI" || warn "memdex was not installed"
    dim "Project data in .memdex/ is left alone. Remove it with: memdex clean --all"
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. install
# ---------------------------------------------------------------------------
if [ "$DEV" -eq 1 ]; then
    step "2. Setting up a development environment"
    (cd "$SCRIPT_DIR" && uv sync --all-extras)
    ok "Dependencies installed into .venv"

    step "3. Running the test suite"
    (cd "$SCRIPT_DIR" && uv run pytest -q)
    ok "Tests passed"

    step "4. Linting"
    (cd "$SCRIPT_DIR" && uv run ruff check .)
    ok "Lint clean"

    MEMDEX="$SCRIPT_DIR/.venv/bin/memdex"
else
    step "2. Installing the memdex CLI"
    # --with tiktoken = the [tokens] extra: exact token counts, not estimates.
    uv tool install --force --with tiktoken "$SCRIPT_DIR" >/dev/null
    ok "Installed memdex $(uv tool run --from "$SCRIPT_DIR" memdex --version 2>/dev/null | awk '{print $2}' || echo '')"

    if ! command -v memdex >/dev/null 2>&1; then
        warn "memdex is installed but not on your PATH yet."
        dim "  Run: uv tool update-shell   (then open a new terminal)"
    fi
    MEMDEX="$(command -v memdex || echo "$HOME/.local/bin/memdex")"
fi

# ---------------------------------------------------------------------------
# 3. verify
# ---------------------------------------------------------------------------
step "Verifying the install"
if [ -x "$MEMDEX" ] || command -v memdex >/dev/null 2>&1; then
    "$MEMDEX" --version >/dev/null && ok "memdex runs"
else
    die "Could not run memdex at $MEMDEX"
fi

if [ "$PREFETCH" -eq 1 ]; then
    step "Downloading the embedding model (one time, ~80MB)"
    if "$MEMDEX" --version >/dev/null 2>&1 && uv run --directory "$SCRIPT_DIR" python -c "
from memdex.embed.local import LocalEmbedder
LocalEmbedder().embed(['warmup'])
print('cached')
" >/dev/null 2>&1; then
        ok "Embedding model cached — Memdex now works fully offline"
    else
        warn "Could not fetch the model. Memdex will fall back to the offline hash embedder."
        dim "  Retry later, or use --offline / embedding.provider: hash."
    fi
fi

# ---------------------------------------------------------------------------
# 4. what next
# ---------------------------------------------------------------------------
INIT_FLAGS=""
[ "$WITH_UI" -eq 0 ] && INIT_FLAGS="$INIT_FLAGS --no-ui"
[ "$OFFLINE" -eq 1 ] && INIT_FLAGS="$INIT_FLAGS --offline"

step "Done"
cat <<EOF
In any code project:

  ${BOLD}memdex init${INIT_FLAGS}${OFF}      set up Memdex here
  ${BOLD}memdex run --dry-run${OFF}   see what it would change
  ${BOLD}memdex run${OFF}             optimize, index and embed your memory
  ${BOLD}memdex search "why did we choose X?"${OFF}

Then:

  ${BOLD}memdex history${OFF}         token savings over time, in the terminal
EOF
if [ "$WITH_UI" -eq 1 ]; then
    printf '  %smemdex ui%s              the same view in a local dashboard\n' "$BOLD" "$OFF"
else
    printf '  %sthe dashboard is off%s   enable it with ui.enabled: true in .memdex/config.yaml\n' "$DIM" "$OFF"
fi
cat <<EOF
  ${BOLD}memdex doctor${OFF}          check that everything is wired up
  ${BOLD}memdex clean${OFF}           wipe the local database (markdown files are kept)

Try it on the bundled example:

  cd "$SCRIPT_DIR/examples/demo-project" && memdex init --here && memdex run

EOF
