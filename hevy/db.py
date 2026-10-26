"""SQLite access and versioned migrations (hevy/migrations/NNNN_name.sql, applied in order).

Same approach as fin and speedtest: one file in the service's private data directory, WAL, every
migration in its own transaction and recorded in schema_migrations. Two services share the file (the
sync worker writes Hevy data, the web app writes only small request rows), so the directory is
group-writable for the shared group and files are created 0660.
"""
from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS = Path(__file__).parent / "migrations"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    @contextmanager
    def tx(self):
        """One atomic unit of work: BEGIN IMMEDIATE ... COMMIT (ROLLBACK on any error)."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self):
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> list[str]:
        """Apply pending migrations; returns the names applied now."""
        self.path.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
        if not self.path.exists():                      # never world-readable, whatever the umask
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o660))
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                         "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')))")
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            applied = []
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
                version = int(path.name[:4])
                if version in done:
                    continue
                sql = path.read_text()
                if re.search(r"\bBEGIN\s+(IMMEDIATE|EXCLUSIVE|DEFERRED|TRANSACTION)\b|\bBEGIN\s*;|\bCOMMIT\b",
                             re.sub(r"--[^\n]*", "", sql), re.I):
                    raise RuntimeError(f"{path.name}: migrations must not manage transactions")
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                    conn.execute("ROLLBACK")             # the other service applied it while we waited for the lock
                    continue
                try:
                    for statement in _statements(sql):
                        conn.execute(statement)
                    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (version, path.stem))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                applied.append(path.stem)
            problems = conn.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"foreign key check failed: {len(problems)} rows")
            return applied
        finally:
            conn.close()


def _statements(sql: str) -> list[str]:
    """Split a migration into statements; trigger bodies (BEGIN ... END;) stay whole."""
    out, buf = [], []
    for line in sql.splitlines(keepends=True):
        buf.append(line)
        text = "".join(buf)
        if sqlite3.complete_statement(text):
            stmt = text.strip()
            if re.sub(r"--[^\n]*", "", stmt).strip():
                out.append(stmt)
            buf = []
    tail = re.sub(r"--[^\n]*", "", "".join(buf)).strip()
    if tail:
        raise RuntimeError("migration ends with an incomplete statement")
    return out


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute("INSERT INTO app_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
