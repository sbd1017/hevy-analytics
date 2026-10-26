"""Next-session estimates (docs/METRICS.md, "Next-session estimate"). Experimental, and labelled as such.

Chance of a new best e1RM next time = **how often that happened in your own history in the same situation**:
how close the last session was to the best (within 2%, 2–5%, 5–10%, 10%+ below) and whether the recent trend was
rising. It is a frequency you can check, not a black box. A first version that projected the trend line and assumed
normal noise was tried and rejected: on your history it was badly overconfident (it said 90% when new bests happened
under 20% of the time) and scored worse than always guessing the average rate.

`backtest` checks the frequency model honestly: every exercise is predicted from the *other* exercises' history only,
at every past session, and scored against what happened (calibration bins and a Brier score vs. the base rate).
The trend line is still shown, as "what the trend points to", with the set you'd need to beat your best.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .insights import DAY_MS, _sessions
from .queries import r1
from .stats import median, theil_sen

MIN_HISTORY = 6                    # sessions with an e1RM before a situation is counted
WINDOW = 8                         # trend over the last 8 sessions
LONG_GAP_DAYS = 60                 # a session after a longer gap isn't a "next session" in the usual sense
MIN_CELL = 30                      # fewer cases than this in gap band x trend: fall back to the gap band alone
BANDS = [(0.0, 0.02, "within 2% of your best"), (0.02, 0.05, "2–5% below your best"),
         (0.05, 0.10, "5–10% below your best"), (0.10, 99.0, "10% or more below your best")]


def _trend(prior: list[dict]) -> float:
    w = prior[-WINDOW:]
    t0 = w[0]["start_ms"]
    return theil_sen([((s["start_ms"] - t0) / DAY_MS, s["best_e1rm_kg"]) for s in w]) or 0.0


def situation(prior: list[dict]) -> tuple[int, bool, float, float]:
    """(gap band index, rising?, gap fraction, best) for the session after `prior`."""
    best = max(s["best_e1rm_kg"] for s in prior)
    gap = (best - prior[-1]["best_e1rm_kg"]) / best
    band = next(i for i, (lo, hi, _) in enumerate(BANDS) if lo <= gap < hi)
    return band, _trend(prior) > 0, gap, best


def _cases(conn) -> list[tuple[str, int, bool, int]]:
    """Every past (exercise, band, rising, new best?) case in the history."""
    out = []
    for tid, ss in _sessions(conn).items():
        e = [s for s in ss if s["best_e1rm_kg"]]
        for k in range(MIN_HISTORY, len(e)):
            prior = e[:k]
            if (e[k]["start_ms"] - prior[-1]["start_ms"]) / DAY_MS > LONG_GAP_DAYS:
                continue
            band, rising, _, best = situation(prior)
            out.append((tid, band, rising, 1 if e[k]["best_e1rm_kg"] > best else 0))
    return out


def _tables(cases):
    cell, band = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for _, b, r, y in cases:
        cell[(b, r)][0] += y; cell[(b, r)][1] += 1
        band[b][0] += y; band[b][1] += 1
    return cell, band


def _estimate(cell, band, b: int, rising: bool):
    hits, n = cell.get((b, rising), (0, 0))
    if n >= MIN_CELL:
        return hits / n, n, True
    hits, n = band.get(b, (0, 0))
    return (hits / n if n else None), n, False


def next_session(conn, template_id: str, today_ms: int | None = None) -> dict:
    ss = _sessions(conn, template_id).get(template_id, [])
    e = [s for s in ss if s["best_e1rm_kg"]]
    if len(e) < MIN_HISTORY + 2:
        return {"available": False, "reason": f"needs {MIN_HISTORY + 2} sessions with an estimated 1RM (has {len(e)})"}
    cases = _cases(conn)
    cell, band = _tables(cases)
    b, rising, gap, best = situation(e)
    p, n, specific = _estimate(cell, band, b, rising)
    own = [y for tid, bb, _, y in cases if tid == template_id and bb == b]
    last = e[-1]["best_e1rm_kg"]
    slope = _trend(e)
    gaps = [(y["start_ms"] - x["start_ms"]) / DAY_MS for x, y in zip(e[-WINDOW:], e[-WINDOW + 1:])]
    expected = median([s["best_e1rm_kg"] for s in e[-3:]]) + slope * (median(gaps) or 7)
    sets = conn.execute("SELECT weight_kg, reps FROM an_sets WHERE template_id = ? AND e1rm_kg IS NOT NULL ORDER BY start_ms DESC LIMIT 60",
                        (template_id,)).fetchall()
    reps = int(round(median([r["reps"] for r in sets]) or 8))
    step = _step([r["weight_kg"] for r in sets])
    factor = 1 + reps / 30.0
    beat = math.ceil(best / factor / step) * step
    while beat * factor <= best:
        beat += step
    days_since = (today_ms - e[-1]["start_ms"]) / DAY_MS if today_ms else 0
    situation_text = f"your last session was {BANDS[b][2]}" + (f" and the trend was {'rising' if rising else 'flat or falling'}" if specific else "")
    reasons = [f"In your history, when {situation_text}, the next session set a new best e1RM {p * 100:.0f}% of the time ({n} cases, all exercises)."]
    if len(own) >= 10:
        reasons.append(f"For this exercise alone, in the same position: {sum(own) / len(own) * 100:.0f}% ({len(own)} cases).")
    reasons.append(f"Last session {last:.1f} kg e1RM vs your best {best:.1f} kg ({gap * 100:.1f}% below). Trend {slope * 30.44:+.1f} kg a month.")
    if days_since > LONG_GAP_DAYS:
        reasons.append(f"It's been {days_since:.0f} days since you last did it, longer than any gap in these figures, so this is a loose guide.")
    return {
        "available": True, "experimental": True,
        "chance_new_best_pct": round(p * 100) if p is not None else None, "cases": n, "situation": situation_text,
        "confidence": "low" if n < 100 or days_since > LONG_GAP_DAYS else "moderate",
        "best_e1rm": r1(best), "last_e1rm": r1(last), "trend_kg_per_month": r1(slope * 30.44),
        "trend_points_to": {"e1rm": r1(expected), "weight_kg": round(expected / factor / step) * step, "reps": reps},
        "to_beat_best": {"weight_kg": beat, "reps": reps, "load_step_kg": step},
        "reasons": reasons,
    }


def _step(weights: list[float]) -> float:
    """The load increment the exercise is usually done in (smallest common gap between loads), default 2.5 kg."""
    ws = sorted({round(w, 2) for w in weights if w})
    diffs = [round(b - a, 2) for a, b in zip(ws, ws[1:]) if b - a >= 0.5]
    if not diffs:
        return 2.5
    common = median(diffs)
    return min([d for d in diffs if d <= common] or [2.5])


def backtest(conn) -> dict:
    """Each exercise predicted from the other exercises' history only; scored on what happened."""
    cases = _cases(conn)
    if len(cases) < 50:
        return {"available": False, "reason": f"needs 50 past cases (has {len(cases)})", "n": len(cases)}
    cell, band = _tables(cases)
    own_cell, own_band = defaultdict(lambda: defaultdict(lambda: [0, 0])), defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for tid, b, r, y in cases:
        own_cell[tid][(b, r)][0] += y; own_cell[tid][(b, r)][1] += 1
        own_band[tid][b][0] += y; own_band[tid][b][1] += 1
    scored = []
    for tid, b, r, y in cases:
        c = {k: (v[0] - own_cell[tid][k][0], v[1] - own_cell[tid][k][1]) for k, v in cell.items()}
        bd = {k: (v[0] - own_band[tid][k][0], v[1] - own_band[tid][k][1]) for k, v in band.items()}
        p, n, _ = _estimate(c, bd, b, r)
        if p is not None and n:
            scored.append((p, y))
    base = sum(y for _, y in scored) / len(scored)
    bins = []
    for lo, hi in ((0, .1), (.1, .2), (.2, .3), (.3, 1.01)):
        inb = [(p, y) for p, y in scored if lo <= p < hi]
        if inb:
            bins.append({"range": f"{lo * 100:.0f}–{min(hi, 1) * 100:.0f}%", "predicted_pct": round(sum(p for p, _ in inb) / len(inb) * 100, 1),
                         "happened_pct": round(sum(y for _, y in inb) / len(inb) * 100, 1), "n": len(inb)})
    brier = sum((p - y) ** 2 for p, y in scored) / len(scored)
    brier_base = sum((base - y) ** 2 for _, y in scored) / len(scored)
    return {"available": True, "n": len(scored), "bins": bins, "brier": round(brier, 4), "brier_baseline": round(brier_base, 4),
            "base_rate_pct": round(base * 100, 1), "better_than_baseline": brier < brier_base,
            "table": [{"situation": BANDS[b][2], "rising": r, "new_best_pct": round(v[0] / v[1] * 100, 1), "cases": v[1]}
                      for (b, r), v in sorted(cell.items()) if v[1]]}
