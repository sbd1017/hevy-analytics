"""The raw layer: every distinct payload Hevy sent (append-only), deletions, and which version is current.

Rules for which version is current:
* Workouts carry `updated_at`. A payload becomes current only if it is newer than the current one (an older
  payload arriving late is still stored as a version, but changes nothing). A deletion wins over any payload
  updated before it; a payload updated after a deletion brings the workout back.
* Templates, routines and body measurements: whatever Hevy most recently sent is current.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .timeutil import iso, later, utcnow

KINDS_WITH_UPDATED_AT = {"workout"}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def object_key(kind: str, obj: dict) -> str:
    key = obj.get("date") if kind == "body_measurement" else obj.get("id")
    if not isinstance(key, str) or not key or len(key) > 128:
        raise ValueError(f"{kind} without a usable id")
    return key


def store_version(conn: sqlite3.Connection, kind: str, obj: dict, run_id: int | None) -> tuple[int, bool]:
    """Store this payload unless the identical one is already stored. Returns (raw_object_id, newly_stored)."""
    hevy_id = object_key(kind, obj)
    payload = canonical(obj)
    sha = hashlib.sha256(payload.encode()).hexdigest()
    updated = obj.get("updated_at") if isinstance(obj.get("updated_at"), str) else None
    cur = conn.execute("INSERT OR IGNORE INTO raw_objects (kind, hevy_id, payload, sha256, hevy_updated_at, fetched_at, "
                       "sync_run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (kind, hevy_id, payload, sha, updated, iso(utcnow()), run_id))
    if cur.rowcount:
        return cur.lastrowid, True
    row = conn.execute("SELECT id FROM raw_objects WHERE kind = ? AND hevy_id = ? AND sha256 = ?",
                       (kind, hevy_id, sha)).fetchone()
    return row[0], False


def apply_object(conn: sqlite3.Connection, kind: str, obj: dict, run_id: int | None) -> dict:
    """Store a payload and move the current pointer if it should be current.

    Returns {"id", "new_version", "changed"}; changed means the current state of this object changed."""
    raw_id, new_version = store_version(conn, kind, obj, run_id)
    hevy_id = object_key(kind, obj)
    updated = obj.get("updated_at") if kind in KINDS_WITH_UPDATED_AT else None
    cur = conn.execute("SELECT raw_object_id, hevy_updated_at, deleted, tombstone_id FROM raw_current "
                       "WHERE kind = ? AND hevy_id = ?", (kind, hevy_id)).fetchone()
    if cur is None:
        conn.execute("INSERT INTO raw_current (kind, hevy_id, raw_object_id, hevy_updated_at, deleted) "
                     "VALUES (?, ?, ?, ?, 0)", (kind, hevy_id, raw_id, updated))
        return {"id": hevy_id, "new_version": new_version, "changed": True}
    if kind in KINDS_WITH_UPDATED_AT:
        if cur["deleted"]:
            tomb = conn.execute("SELECT deleted_at FROM raw_tombstones WHERE id = ?", (cur["tombstone_id"],)).fetchone()
            if not later(updated, tomb["deleted_at"] if tomb else None):
                return {"id": hevy_id, "new_version": new_version, "changed": False}   # stale: deleted afterwards
        elif raw_id == cur["raw_object_id"] or later(cur["hevy_updated_at"], updated):
            return {"id": hevy_id, "new_version": new_version, "changed": False}       # same, or older than current
    elif raw_id == cur["raw_object_id"] and not cur["deleted"]:
        return {"id": hevy_id, "new_version": new_version, "changed": False}
    conn.execute("UPDATE raw_current SET raw_object_id = ?, hevy_updated_at = ?, deleted = 0, tombstone_id = NULL "
                 "WHERE kind = ? AND hevy_id = ?", (raw_id, updated, kind, hevy_id))
    return {"id": hevy_id, "new_version": new_version, "changed": True}


def apply_deletion(conn: sqlite3.Connection, kind: str, hevy_id: str, deleted_at: str, source: str,
                   run_id: int | None) -> bool:
    """Record a deletion. Returns True if the object was live and is now deleted."""
    conn.execute("INSERT OR IGNORE INTO raw_tombstones (kind, hevy_id, deleted_at, source, recorded_at, sync_run_id) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (kind, hevy_id, deleted_at, source, iso(utcnow()), run_id))
    tomb_id = conn.execute("SELECT id FROM raw_tombstones WHERE kind = ? AND hevy_id = ? AND deleted_at = ?",
                           (kind, hevy_id, deleted_at)).fetchone()[0]
    cur = conn.execute("SELECT hevy_updated_at, deleted FROM raw_current WHERE kind = ? AND hevy_id = ?",
                       (kind, hevy_id)).fetchone()
    if cur is None or cur["deleted"]:
        return False                       # never seen (deleted before we synced it), or already deleted
    if kind in KINDS_WITH_UPDATED_AT and later(cur["hevy_updated_at"], deleted_at):
        return False                       # edited after this deletion: the deletion is stale
    conn.execute("UPDATE raw_current SET deleted = 1, tombstone_id = ? WHERE kind = ? AND hevy_id = ?",
                 (tomb_id, kind, hevy_id))
    return True


def live_ids(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT hevy_id FROM raw_current WHERE kind = ? AND deleted = 0", (kind,))}


def current_payload(conn: sqlite3.Connection, kind: str, hevy_id: str) -> tuple[dict | None, bool, int | None]:
    """(payload, deleted, raw_object_id) of the current version; (None, False, None) if never seen."""
    row = conn.execute("SELECT c.deleted, o.id, o.payload FROM raw_current c JOIN raw_objects o ON o.id = c.raw_object_id "
                       "WHERE c.kind = ? AND c.hevy_id = ?", (kind, hevy_id)).fetchone()
    if row is None:
        return None, False, None
    return json.loads(row["payload"]), bool(row["deleted"]), row["id"]
