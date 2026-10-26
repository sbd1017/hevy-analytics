from datetime import datetime, timezone

from hevy import cli
from hevy.backup import make_backup, restore_check


def synced(engine, fake, n=3):
    for i in range(n):
        fake.add_workout(datetime(2026, 9, 1 + i, 17, 0, tzinfo=timezone.utc))
    assert engine.run("backfill", "test")["status"] == "ok"


def test_backup_and_restore_check(engine, fake, settings):
    synced(engine, fake)
    paths = [make_backup(settings.db_path, settings.backup_dir, keep=2) for _ in range(3)]
    kept = sorted(settings.backup_dir.glob("hevy-*.sqlite3"))
    assert len(kept) <= 2 and paths[-1] in kept
    out = restore_check(paths[-1])
    assert out["ok"] and out["workouts"] == 3 and out["raw_objects"] > 3


def test_verify_passes_then_detects_drift(engine, fake, settings, db, monkeypatch, capsys):
    synced(engine, fake)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    assert cli.main(["verify"]) == 0
    assert "RESULT: OK" in capsys.readouterr().out
    with db.tx() as c:
        c.execute("UPDATE sets SET weight_kg = 999 WHERE id = (SELECT min(id) FROM sets)")
    assert cli.main(["verify"]) == 1
    assert "DIFFERS" in capsys.readouterr().out
    assert cli.main(["rebuild-normalised"]) == 0
    assert cli.main(["verify"]) == 0


def test_import_dump_refused_in_production(settings, monkeypatch, tmp_path):
    settings.env = "production"
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    dump = tmp_path / "d.json"
    dump.write_text("{}")
    assert cli.main(["import-dump", str(dump)]) == 2


def test_status_reports_offsite_copy(engine, fake, settings, db):
    import json as _json
    from hevy.status import build
    synced(engine, fake)
    assert build(db, settings)["offsite"] is None
    (settings.data_dir / "offsite.json").write_text(_json.dumps({"at": "2020-01-01T00:00:00Z", "file": "x.gz", "copies": 3}))
    off = build(db, settings)["offsite"]
    assert off["copies"] == 3 and off["stale"] is True
