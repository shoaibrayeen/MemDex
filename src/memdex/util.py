"""Small filesystem/string helpers shared across modules."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_stamp() -> str:
    """Timestamp usable as a directory name on every platform (no colons)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".memdex-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_within(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` resolves inside ``root``."""
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def relpath_in(root: Path, path: Path) -> Path:
    """Path relative to root; falls back to the absolute path when outside."""
    try:
        return path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return path


def humanize_stem(stem: str) -> str:
    """``api-notes`` -> ``Api Notes``; used to title headingless files."""
    cleaned = stem.replace("_", " ").replace("-", " ").replace(".", " ").strip()
    if not cleaned:
        return "Untitled"
    words = []
    for word in cleaned.split():
        # MEMORY -> Memory, but leave mixed case like "myApp" alone.
        words.append(word.capitalize() if word.isupper() else word[:1].upper() + word[1:])
    return " ".join(words)


def fmt_int(value: int) -> str:
    return f"{value:,}"


def plural(count: int, singular: str, many: str | None = None) -> str:
    """"1 duplicate" / "3 duplicates" — small thing, but the output is the product."""
    word = singular if count == 1 else (many or f"{singular}s")
    return f"{fmt_int(count)} {word}"


def fmt_pct(value: float) -> str:
    return f"{value:.1f}%"
