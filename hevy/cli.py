"""Command line: ``python -m hevy.cli <command>`` (run as the sync worker's user, with its environment).

  status                 sync health, counts, recent runs
  sync [--kind K]        run a sync now (incremental | backfill | reconcile | reference); needs the API key
  request-sync           ask the running worker to sync (what the web page's button does)
  rebuild-normalised     recreate workouts/exercises/sets/templates from the raw layer (no Hevy calls)
  rebuild-analytics      recalculate every derived table with the current rules (docs/METRICS.md)
  verify                 integrity check, raw pointers, and "would a rebuild give the same result?"
  backup [--keep N]      consistent copy into var/backups/
  restore-check FILE     check a backup without changing it
  import-dump FILE       development only: load a JSON dump of real data (never in production)
  demo-seed              fill an EMPTY database with made-up history (the public demo; HEVY_DEMO=1 or development)
  gpt-key new|revoke|status   the key your own GPT in ChatGPT uses to reach /hevy/gpt/ (docs/CHATGPT.md);
                         "new" prints it once (only a hash is kept) and replaces any earlier key
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from . import coach, normalise, rawstore
from .analytics import build as analytics
from .backup import make_backup, restore_check
from .client import AuthError, HevyClient, load_key
from .config import load_settings
from .db import Database, get_meta, set_meta
from .status import build as build_status
from .sync import Busy, SyncEngine, set_state
from .timeutil import iso, iso_s, utcnow

NORMALISED_QUERIES = {
    "workouts": "SELECT id, title, description, routine_id, start_utc, end_utc, duration_s, local_date, raw_object_id FROM workouts ORDER BY id",
    "exercises": "SELECT workout_id, position, template_id, title, notes, superset_id FROM workout_exercises ORDER BY workout_id, position",
    "sets": "SELECT s.workout_id, e.position, s.position, set_type, weight_kg, reps, distance_m, duration_s, rpe, custom_metric "
            "FROM sets s JOIN workout_exercises e ON e.id = s.workout_exercise_id ORDER BY 1, 2, 3",
    "templates": "SELECT id, title, type, primary_muscle, equipment, is_custom, known_to_hevy FROM exercise_templates ORDER BY id",
    "template_muscles": "SELECT * FROM template_muscles ORDER BY 1, 2",
    "routines": "SELECT id, title, folder_id, updated_utc FROM routines ORDER BY id",
    "body": "SELECT date, weight_kg, fat_percent, lean_mass_kg, other_json FROM body_measurements ORDER BY date",
    "issues": "SELECT kind, hevy_id, severity, code, detail FROM data_issues ORDER BY 1, 2, 4, 5",
}


def fingerprint(conn) -> dict:
    out = {}
    for name, sql in NORMALISED_QUERIES.items():
        h = hashlib.sha256()
        n = 0
        for row in conn.execute(sql):
            h.update(json.dumps(list(row)).encode())
            n += 1
        out[name] = (n, h.hexdigest()[:16])
    return out


def cmd_verify(db: Database, settings) -> int:
    ok = True
    with db.read() as c:
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        print("integrity_check:", integrity)
        ok &= integrity == "ok"
        bad = c.execute("SELECT count(*) FROM raw_current r JOIN raw_objects o ON o.id = r.raw_object_id "
                        "WHERE o.kind != r.kind OR o.hevy_id != r.hevy_id").fetchone()[0]
        print("raw pointers mismatched:", bad)
        ok &= bad == 0
        current = fingerprint(c)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "verify.sqlite3"
        make_backup(settings.db_path, Path(tmp) / "b", keep=1)
        shutil.copy(next((Path(tmp) / "b").glob("hevy-*.sqlite3")), copy)
        scratch = Database(copy)
        with scratch.tx() as c:
            normalise.rebuild_all(c, settings.timezone)
        with scratch.read() as c:
            rebuilt = fingerprint(c)
    for name in NORMALISED_QUERIES:
        same = current[name] == rebuilt[name]
        ok &= same
        print(f"{name:17} rows {current[name][0]:>7}  {'same as a rebuild from raw' if same else 'DIFFERS from a rebuild: run rebuild-normalised'}")
    print("RESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def cmd_import_dump(db: Database, settings, path: Path) -> int:
    if settings.is_production:
        print("import-dump is for development only", file=sys.stderr)
        return 2
    data = json.loads(Path(path).read_text())
    with db.tx() as c:
        if get_meta(c, "data_origin") not in (None, "hevy-dump"):
            print("this database already holds other data", file=sys.stderr)
            return 2
        set_meta(c, "data_origin", "hevy-dump")
        run = c.execute("INSERT INTO sync_runs (kind, trigger, started_at, status) VALUES ('backfill', 'cli', ?, 'running')",
                        (iso(utcnow()),)).lastrowid
        for kind, key in (("exercise_template", "templates"), ("routine", "routines"), ("body_measurement", "body"),
                          ("workout", "workouts")):
            for obj in data.get(key, []):
                rawstore.apply_object(c, kind, obj, run)
        counts = normalise.rebuild_all(c, settings.timezone)
        c.execute("UPDATE sync_runs SET status = 'ok', finished_at = ?, workouts_seen = ? WHERE id = ?",
                  (iso(utcnow()), len(data.get("workouts", [])), run))
        set_state(c, "last_success_at", iso(utcnow()))
        set_state(c, "cursor", iso_s(utcnow()))
    print("imported", counts)
    print("analytics:", analytics.refresh(db, force=True))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m hevy.cli", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("sync")
    s.add_argument("--kind", default="incremental", choices=["incremental", "backfill", "reconcile", "reference"])
    sub.add_parser("request-sync")
    sub.add_parser("rebuild-normalised")
    sub.add_parser("rebuild-analytics")
    sub.add_parser("verify")
    b = sub.add_parser("backup")
    b.add_argument("--keep", type=int, default=30)
    r = sub.add_parser("restore-check")
    r.add_argument("file", type=Path)
    i = sub.add_parser("import-dump")
    i.add_argument("file", type=Path)
    sub.add_parser("demo-seed")
    g = sub.add_parser("gpt-key", help="the key your GPT in ChatGPT uses (docs/CHATGPT.md)")
    g.add_argument("action", choices=["new", "revoke", "status"])
    args = p.parse_args(argv)

    settings = load_settings()
    if args.cmd == "restore-check":
        out = restore_check(args.file)
        print(json.dumps(out, indent=2))
        print("RESULT:", "OK" if out["ok"] else "NOT OK")
        return 0 if out["ok"] else 1
    db = Database(settings.db_path)
    db.migrate()
    if args.cmd == "status":
        print(json.dumps(build_status(db, settings), indent=2))
    elif args.cmd == "sync":
        try:
            key = load_key(settings.api_key_path())
        except AuthError as exc:
            print(f"no API key: {exc}", file=sys.stderr)
            return 2
        engine = SyncEngine(db, HevyClient(key, settings.api_base, gap_s=settings.request_gap_s), settings)
        try:
            res = engine.run(args.kind, "cli")
        except Busy:
            print("another sync is running; try again shortly", file=sys.stderr)
            return 1
        print(json.dumps(res, indent=2))
        if res["status"] == "ok":
            print("analytics:", analytics.refresh(db, force=True))
        return 0 if res["status"] == "ok" else 1
    elif args.cmd == "request-sync":
        with db.tx() as c:
            c.execute("INSERT INTO sync_requests (requested_at, source) VALUES (?, 'cli')", (iso(utcnow()),))
        print("requested: the worker picks it up within a few seconds")
    elif args.cmd == "rebuild-normalised":
        with db.tx() as c:
            print("rebuilt", normalise.rebuild_all(c, settings.timezone))
        print("analytics:", analytics.refresh(db, force=True))
    elif args.cmd == "rebuild-analytics":
        print("analytics:", analytics.refresh(db, force=True))
    elif args.cmd == "verify":
        return cmd_verify(db, settings)
    elif args.cmd == "backup":
        path = make_backup(settings.db_path, settings.backup_dir, args.keep)
        print("backup:", path)
    elif args.cmd == "import-dump":
        return cmd_import_dump(db, settings, args.file)
    elif args.cmd == "demo-seed":
        if not (settings.demo or not settings.is_production):
            print("demo-seed runs only with HEVY_DEMO=1 or outside production", file=sys.stderr)
            return 2
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from . import demo
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        print(json.dumps(demo.seed(db, today, settings.timezone)))
        now = utcnow()
        coach.refresh(db, settings, "stale", now, today, None)
    elif args.cmd == "gpt-key":
        with db.tx() as c:
            if args.action == "new":
                key = coach.new_gpt_key(c)
                print("New key (shown once; paste it into your GPT's Action as an API key, auth type Bearer):\n")
                print(key)
                print("\nAny earlier key has stopped working.")
            elif args.action == "revoke":
                coach.revoke_gpt_key(c)
                print("revoked: /hevy/gpt/ now refuses every key")
            else:
                print("key set" if get_meta(c, "gpt_key_sha256") else "no key", "| created", get_meta(c, "gpt_key_created_at"),
                      "| last used", get_meta(c, "gpt_last_used_at"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
