"""The Strength Index: a personal, transparent composite of how strong you are relative to your own starting points
(docs/METRICS.md, "Strength Index"). Not an absolute measure, and not comparable between people.

1. Components: up to 8 exercises with an estimated 1RM, chosen for being done most often (at least 8 sessions), with
   at most 2 per main muscle so one body part can't dominate.
2. Each component's level in a week = the median of its last 3 sessions' best e1RM up to that week. It counts as
   active while it was done within the last 90 days.
3. The index starts at 100 and is chain-linked like a price index: each week it moves by the geometric mean of the
   week-on-week change in level of the components active in both weeks (at least 3 needed; otherwise it holds).
   So exercises starting, stopping or coming back never make it jump, only real changes in level do.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .insights import DAY_MS, _sessions
from .queries import pct, r1, titles
from .stats import median

MAX_COMPONENTS = 8
MIN_SESSIONS = 8
PER_MUSCLE = 2
ACTIVE_DAYS = 90
MIN_ACTIVE = 3


def components(conn) -> list[dict]:
    ss = _sessions(conn)
    cands = []
    for tid, rows in ss.items():
        e = [s for s in rows if s["best_e1rm_kg"]]
        if len(e) >= MIN_SESSIONS:
            cands.append((len(e), tid, rows[-1]["primary_muscle"], e))
    cands.sort(key=lambda c: (-c[0], c[1]))
    chosen, per = [], {}
    for n, tid, muscle, e in cands:
        if per.get(muscle, 0) >= PER_MUSCLE:
            continue
        per[muscle] = per.get(muscle, 0) + 1
        chosen.append({"template_id": tid, "muscle": muscle, "sessions": e})
        if len(chosen) == MAX_COMPONENTS:
            break
    return chosen


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def strength_index(conn) -> dict:
    comps = components(conn)
    if len(comps) < MIN_ACTIVE:
        return {"available": False, "reason": f"needs {MIN_ACTIVE} exercises with {MIN_SESSIONS}+ e1RM sessions (has {len(comps)})", "components": []}
    first = min(_monday(date.fromisoformat(c["sessions"][0]["local_date"])) for c in comps)
    last = max(_monday(date.fromisoformat(c["sessions"][-1]["local_date"])) for c in comps)
    weeks = []
    w = first
    while w <= last:
        weeks.append(w)
        w += timedelta(weeks=1)

    def level(c, week_end: date):
        done = [s for s in c["sessions"] if s["local_date"] <= week_end.isoformat()]
        if not done or (week_end - date.fromisoformat(done[-1]["local_date"])).days > ACTIVE_DAYS:
            return None
        return median([s["best_e1rm_kg"] for s in done[-3:]])

    series, index, prev = [], None, {}
    for wk in weeks:
        end = wk + timedelta(days=6)
        cur = {c["template_id"]: level(c, end) for c in comps}
        cur = {k: v for k, v in cur.items() if v}
        common = [k for k in cur if k in prev]
        if index is None:
            if len(cur) >= MIN_ACTIVE:
                index = 100.0
        elif len(common) >= MIN_ACTIVE:
            index *= math.exp(sum(math.log(cur[k] / prev[k]) for k in common) / len(common))
        if index is not None:
            series.append({"week": wk.isoformat(), "index": round(index, 1), "active": len(cur), "linked": len(common)})
        prev = cur
    names = titles(conn)
    out_c = []
    last_week_end = weeks[-1] + timedelta(days=6)
    for c in comps:
        e = c["sessions"]
        base = median([s["best_e1rm_kg"] for s in e[:3]])
        now = median([s["best_e1rm_kg"] for s in e[-3:]])
        out_c.append({"template_id": c["template_id"], "title": names.get(c["template_id"]), "muscle": c["muscle"], "sessions": len(e),
                      "first": e[0]["local_date"], "last": e[-1]["local_date"], "start_level": r1(base), "current_level": r1(now),
                      "change_pct": pct(now, base), "active": (last_week_end - date.fromisoformat(e[-1]["local_date"])).days <= ACTIVE_DAYS})
    cur_val = series[-1]["index"] if series else None
    ago = lambda weeks_back: next((p["index"] for p in reversed(series[:-weeks_back] if weeks_back < len(series) else [])), None)
    peak = max(series, key=lambda p: p["index"]) if series else None
    return {"available": bool(series), "current": cur_val, "as_of_week": series[-1]["week"] if series else None,
            "change_13w_pct": pct(cur_val, ago(13)), "change_52w_pct": pct(cur_val, ago(52)),
            "peak": peak, "series": series, "components": out_c,
            "method": "Chain-linked: each week the index moves by the geometric mean of the week-on-week change in level "
                      "(median of the last 3 sessions' best e1RM) of the component exercises active in both weeks. Starts at 100."}
