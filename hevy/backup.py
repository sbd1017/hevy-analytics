"""Consistent SQLite backups (online backup API), integrity-checked, newest N kept.

The backup holds every raw version received from Hevy, so it preserves history even if something is later
deleted or changed in Hevy. Backups live in var/backups/ (private, never served).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .timeutil import utcnow


def make_backup(db_path: Path, dest_dir: Path, keep: int = 30) -> Path:
    dest_dir.mkdir(mode=0o770, parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    final = dest_dir / f"hevy-{stamp}.sqlite3"
    tmp = dest_dir / f".hevy-{stamp}.sqlite3.part"
    os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660))
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode = DELETE")
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup failed integrity check: {result}")
    finally:
        dst.close()
        src.close()
    os.replace(tmp, final)
    backups = sorted(dest_dir.glob("hevy-*.sqlite3"))
    for old in backups[:-keep] if keep > 0 else []:
        old.unlink()
    return final


def restore_check(path: Path) -> dict:
    """Read-only check that a backup is usable. Never modifies the file."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out = {"integrity": conn.execute("PRAGMA integrity_check").fetchone()[0]}
        out["schema_version"] = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        for table in ("raw_objects", "raw_tombstones", "workouts", "sets", "exercise_templates", "sync_runs"):
            out[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        out["ok"] = out["integrity"] == "ok"
        return out
    finally:
        conn.close()
