"""Backups and restore.

Memdex rewrites files a user may have spent months accumulating, so every
applying run first copies what it is about to touch into
``.memdex/backups/<timestamp>/``. The manifest records which files existed
before (restore puts them back) and which Memdex created (restore removes them),
so a restore returns the tree to exactly its previous shape.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from memdex import __version__
from memdex.errors import BackupError
from memdex.models import BackupInfo, FileOp
from memdex.util import atomic_write, utc_now_iso, utc_stamp

MANIFEST_NAME = "manifest.json"


@dataclass
class RestoreResult:
    restored: list[Path]
    removed: list[Path]
    pre_restore: BackupInfo | None


def create_backup(cfg, ops: list[FileOp], reason: str) -> BackupInfo | None:
    """Copy every file the plan will modify or delete. No ops -> no backup."""
    if not ops:
        return None

    backup_dir = cfg.backups_dir / utc_stamp()
    if backup_dir.exists():  # two runs inside the same second
        backup_dir = cfg.backups_dir / f"{utc_stamp()}-{len(list(cfg.backups_dir.iterdir()))}"

    backed_up: list[Path] = []
    created: list[Path] = []
    for op in ops:
        source = cfg.root / op.path
        if source.exists():
            destination = backup_dir / op.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except OSError as exc:
                raise BackupError(
                    f"Could not back up {op.path}: {exc}",
                    hint="Check permissions on .memdex/backups/ and try again.",
                ) from exc
            backed_up.append(op.path)
        else:
            created.append(op.path)

    info = BackupInfo(
        dir=backup_dir,
        created_at=utc_now_iso(),
        reason=reason,
        backed_up=backed_up,
        created=created,
    )
    _write_manifest(info)
    return info


def _write_manifest(info: BackupInfo) -> None:
    payload = {
        "created_at": info.created_at,
        "reason": info.reason,
        "memdex_version": __version__,
        "backed_up": [p.as_posix() for p in info.backed_up],
        "created": [p.as_posix() for p in info.created],
    }
    atomic_write(info.dir / MANIFEST_NAME, json.dumps(payload, indent=2) + "\n")


def _read_manifest(directory: Path) -> BackupInfo | None:
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return BackupInfo(
        dir=directory,
        created_at=str(data.get("created_at", "")),
        reason=str(data.get("reason", "")),
        backed_up=[Path(p) for p in data.get("backed_up", [])],
        created=[Path(p) for p in data.get("created", [])],
    )


def list_backups(cfg) -> list[BackupInfo]:
    if not cfg.backups_dir.is_dir():
        return []
    found = []
    for directory in sorted(cfg.backups_dir.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        info = _read_manifest(directory)
        if info:
            found.append(info)
    return found


def find_backup(cfg, stamp: str | None, latest: bool) -> BackupInfo:
    backups = list_backups(cfg)
    if not backups:
        raise BackupError(
            "There are no backups to restore from.",
            hint="Backups are created automatically the first time `memdex run` changes a file.",
        )
    if latest or not stamp:
        return backups[0]
    for info in backups:
        if info.stamp == stamp or info.stamp.startswith(stamp):
            return info
    raise BackupError(
        f"No backup named {stamp!r}.",
        hint="Run `memdex restore` with no arguments to see what is available.",
    )


def restore_backup(cfg, info: BackupInfo) -> RestoreResult:
    """Put the tree back. Takes its own backup first, so a restore is reversible."""
    affected = [
        FileOp(path=path, action="overwrite", reason="pre-restore snapshot")
        for path in [*info.backed_up, *info.created]
    ]
    pre_restore = create_backup(cfg, affected, reason=f"pre-restore of {info.stamp}")

    restored: list[Path] = []
    for rel in info.backed_up:
        source = info.dir / rel
        if not source.is_file():
            continue
        target = cfg.root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        restored.append(rel)

    removed: list[Path] = []
    for rel in info.created:
        target = cfg.root / rel
        if target.is_file():
            target.unlink()
            removed.append(rel)

    prune_empty_dirs(cfg.abs_output_dir)
    return RestoreResult(restored=restored, removed=removed, pre_restore=pre_restore)


def prune_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for directory in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()
