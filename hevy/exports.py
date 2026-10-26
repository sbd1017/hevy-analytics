"""Exports of the local history and derived analytics: CSV (one file, or a ZIP of several), JSON, Excel.

Every export is filtered the same way (date range, exercise, muscle, workout) and carries a description of what it
is: when it was made, the filters, the rules version (docs/METRICS.md) and the app version. "All time" is never capped.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date

from . import VERSION
from .analytics.rules import ANALYTICS_VERSION
from .timeutil import iso, utcnow

DATASETS = ("sets", "workouts", "exercises", "records", "days", "raw")
LABELS = {"sets": "Sets", "workouts": "Workouts", "exercises": "Exercises", "records": "Records", "days": "Days", "raw": "Raw Hevy data"}

COLUMNS = {
    "sets": [("date", "a.local_date"), ("start_utc", "w.start_utc"), ("workout_id", "a.workout_id"), ("workout", "w.title"),
             ("exercise_position", "a.ex_position + 1"), ("exercise", "t.title"), ("exercise_id", "a.template_id"),
             ("primary_muscle", "t.primary_muscle"), ("set_number", "a.set_position + 1"), ("set_type", "a.set_type"),
             ("working", "a.working"), ("weight_kg", "a.weight_kg"), ("reps", "a.reps"), ("distance_m", "a.distance_m"),
             ("duration_s", "a.duration_s"), ("rpe", "a.rpe"), ("volume_kg", "a.volume_kg"), ("e1rm_kg", "round(a.e1rm_kg, 2)"),
             ("e1rm_note", "a.e1rm_note"), ("suspect", "a.suspect")],
    "workouts": [("date", "a.local_date"), ("start_utc", "w.start_utc"), ("end_utc", "w.end_utc"), ("workout_id", "a.workout_id"),
                 ("title", "w.title"), ("kind", "a.kind"), ("duration_min", "round(a.duration_s / 60.0, 1)"), ("exercises", "a.exercises"),
                 ("working_sets", "a.working_sets"), ("warmup_sets", "a.warmup_sets"), ("reps", "a.reps"), ("volume_kg", "round(a.volume_kg, 1)"),
                 ("cardio_distance_m", "a.distance_m"), ("cardio_duration_s", "a.cardio_duration_s"), ("avg_rpe", "round(a.avg_rpe, 2)"),
                 ("records", "a.prs"), ("meaningful_records", "a.meaningful_prs"), ("description", "w.description")],
    "exercises": [("exercise", "t.title"), ("exercise_id", "t.id"), ("type", "t.type"), ("primary_muscle", "t.primary_muscle"),
                  ("equipment", "t.equipment"), ("custom", "t.is_custom"), ("sessions", "count(*)"), ("first", "min(s.local_date)"),
                  ("last", "max(s.local_date)"), ("working_sets", "sum(s.working_sets)"), ("reps", "sum(s.reps)"),
                  ("volume_kg", "round(sum(s.volume_kg), 1)"), ("best_e1rm_kg", "round(max(s.best_e1rm_kg), 2)"),
                  ("heaviest_kg", "max(s.top_weight_kg)"), ("distance_m", "sum(s.total_distance_m)")],
    "records": [("date", "p.local_date"), ("exercise", "coalesce(t.title, 'Whole workout')"), ("exercise_id", "p.template_id"),
                ("category", "p.category"), ("value", "round(p.value, 2)"), ("previous", "round(p.previous, 2)"), ("unit", "p.unit"),
                ("detail", "p.detail"), ("early", "p.early"), ("meaningful", "p.meaningful"), ("workout_id", "p.workout_id")],
    "days": [("date", "d.local_date"), ("workouts", "d.workouts"), ("gym_sessions", "d.gym_sessions"), ("cardio_sessions", "d.cardio_sessions"),
             ("duration_min", "round(d.duration_s / 60.0, 1)"), ("working_sets", "d.working_sets"), ("reps", "d.reps"),
             ("volume_kg", "round(d.volume_kg, 1)"), ("distance_m", "d.distance_m"), ("records", "d.meaningful_prs")],
}


def _where(dataset: str, f: dict) -> tuple[str, list]:
    """Filters, as SQL with parameters (values are never interpolated)."""
    col = {"sets": "a.local_date", "workouts": "a.local_date", "exercises": "s.local_date", "records": "p.local_date", "days": "d.local_date"}[dataset]
    where, args = [f"{col} BETWEEN ? AND ?"], [f["start"], f["end"]]
    tid = {"sets": "a.template_id", "exercises": "s.template_id", "records": "p.template_id"}.get(dataset)
    wid = {"sets": "a.workout_id", "workouts": "a.workout_id", "exercises": "s.workout_id", "records": "p.workout_id"}.get(dataset)
    if f.get("exercise") and tid:
        where.append(f"{tid} = ?"); args.append(f["exercise"])
    if f.get("exercise") and dataset == "workouts":
        where.append("a.workout_id IN (SELECT workout_id FROM an_exercise_sessions WHERE template_id = ?)"); args.append(f["exercise"])
    if f.get("muscle") and tid:
        where.append(f"{tid} IN (SELECT template_id FROM template_muscles WHERE muscle = ?)"); args.append(f["muscle"])
    if f.get("muscle") and dataset == "workouts":
        where.append("a.workout_id IN (SELECT s.workout_id FROM an_exercise_sessions s JOIN template_muscles m ON m.template_id = s.template_id "
                     "WHERE m.muscle = ?)"); args.append(f["muscle"])
    if f.get("workout") and wid:
        where.append(f"{wid} = ?"); args.append(f["workout"])
    if f.get("workout") and dataset == "days":
        where.append("d.local_date = (SELECT local_date FROM an_workouts WHERE workout_id = ?)"); args.append(f["workout"])
    return " WHERE " + " AND ".join(where), args


def rows(conn, dataset: str, f: dict) -> tuple[list[str], list[tuple]]:
    cols = COLUMNS[dataset]
    sel = ", ".join(expr for _, expr in cols)
    w, args = _where(dataset, f)
    sql = {
        "sets": f"SELECT {sel} FROM an_sets a JOIN workouts w ON w.id = a.workout_id JOIN exercise_templates t ON t.id = a.template_id{w} "
                "ORDER BY a.start_ms, a.ex_position, a.set_position",
        "workouts": f"SELECT {sel} FROM an_workouts a JOIN workouts w ON w.id = a.workout_id{w} ORDER BY a.start_ms",
        "exercises": f"SELECT {sel} FROM an_exercise_sessions s JOIN exercise_templates t ON t.id = s.template_id{w} GROUP BY t.id ORDER BY count(*) DESC, t.title",
        "records": f"SELECT {sel} FROM an_prs p LEFT JOIN exercise_templates t ON t.id = p.template_id{w} ORDER BY p.start_ms, p.id",
        "days": f"SELECT {sel} FROM an_days d{w} ORDER BY d.local_date",
    }[dataset]
    return [name for name, _ in cols], [tuple(r) for r in conn.execute(sql, args)]


def raw_workouts(conn, f: dict) -> list[dict]:
    """The current Hevy payload of each workout in range (exactly as received), oldest first."""
    sql = ("SELECT o.payload FROM workouts w JOIN raw_objects o ON o.id = w.raw_object_id WHERE w.local_date BETWEEN ? AND ?")
    args = [f["start"], f["end"]]
    if f.get("workout"):
        sql += " AND w.id = ?"; args.append(f["workout"])
    if f.get("exercise"):
        sql += " AND w.id IN (SELECT workout_id FROM workout_exercises WHERE template_id = ?)"; args.append(f["exercise"])
    if f.get("muscle"):
        sql += (" AND w.id IN (SELECT e.workout_id FROM workout_exercises e JOIN template_muscles m ON m.template_id = e.template_id "
                "WHERE m.muscle = ?)"); args.append(f["muscle"])
    return [json.loads(r[0]) for r in conn.execute(sql + " ORDER BY w.start_ms", args)]


def meta(f: dict, datasets: list[str]) -> dict:
    return {"exported_at": iso(utcnow()), "app_version": VERSION, "rules_version": ANALYTICS_VERSION,
            "filters": {k: v for k, v in f.items() if v}, "datasets": datasets, "units": "kg, metres, seconds; dates are local (HEVY_TIMEZONE, Europe/London by default)",
            "notes": "Derived figures follow docs/METRICS.md. Warm-ups are kept with working = 0. Suspect sets are flagged, not removed."}


def _safe(v):
    """Spreadsheet apps execute cells starting with = + - @ as formulas: neutralise text that does."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _csv_bytes(header, data) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows([[_safe(v) for v in r] for r in data])
    return ("﻿" + buf.getvalue()).encode("utf-8")            # BOM so Excel opens UTF-8 (emoji titles) correctly


def build(conn, fmt: str, datasets: list[str], f: dict) -> tuple[bytes, str, str]:
    """Returns (content, media type, file extension)."""
    tables = {d: rows(conn, d, f) for d in datasets if d != "raw"}
    if fmt == "csv":
        if len(tables) == 1 and "raw" not in datasets:
            (header, data), = tables.values()
            return _csv_bytes(header, data), "text/csv; charset=utf-8", "csv"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for d, (header, data) in tables.items():
                z.writestr(f"{d}.csv", _csv_bytes(header, data))
            if "raw" in datasets:
                z.writestr("raw_workouts.json", json.dumps(raw_workouts(conn, f), ensure_ascii=False, indent=1))
            z.writestr("about.json", json.dumps(meta(f, datasets), indent=2))
        return buf.getvalue(), "application/zip", "zip"
    if fmt == "json":
        out = {"about": meta(f, datasets)}
        for d, (header, data) in tables.items():
            out[d] = [dict(zip(header, r)) for r in data]
        if "raw" in datasets:
            out["raw_workouts"] = raw_workouts(conn, f)
        return json.dumps(out, ensure_ascii=False, indent=1).encode(), "application/json", "json"
    if fmt == "xlsx":
        import xlsxwriter
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_urls": False, "strings_to_formulas": False})
        bold = wb.add_format({"bold": True, "bg_color": "#EEF1F5", "border": 1})
        about = wb.add_worksheet("About")
        about.set_column(0, 0, 18); about.set_column(1, 1, 90)
        for i, (k, v) in enumerate(meta(f, datasets).items()):
            about.write(i, 0, k, bold); about.write(i, 1, json.dumps(v) if isinstance(v, (dict, list)) else str(v))
        for d, (header, data) in tables.items():
            ws = wb.add_worksheet(LABELS[d])
            ws.write_row(0, 0, header, bold)
            for r, row in enumerate(data, start=1):
                ws.write_row(r, 0, row)
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(1, len(data)), len(header) - 1)
            for c, name in enumerate(header):
                ws.set_column(c, c, max(10, min(40, len(name) + 4)) if name not in ("workout", "title", "exercise", "description", "suspect", "detail") else 28)
        if "raw" in datasets:
            ws = wb.add_worksheet("Raw workouts (JSON)")
            ws.write_row(0, 0, ["workout_id", "payload"], bold)
            for r, p in enumerate(raw_workouts(conn, f), start=1):
                ws.write_row(r, 0, [p.get("id"), json.dumps(p, ensure_ascii=False)[:32000]])
            ws.set_column(0, 0, 38); ws.set_column(1, 1, 120)
        wb.close()
        return buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    raise ValueError("format must be csv, json or xlsx")


def filename(datasets: list[str], f: dict, ext: str) -> str:
    what = "all" if set(datasets) >= set(DATASETS) - {"raw"} else "-".join(datasets)
    return f"hevy-{what}-{f['start']}-to-{f['end']}.{ext}"
