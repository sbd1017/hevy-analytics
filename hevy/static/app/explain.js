// "How is this calculated?" texts. Kept in step with docs/METRICS.md (analytics rules version 1).
import { h } from './lib.js';

export const EXPLAIN = {
  sessions: ['Gym and cardio sessions', 'A cardio session is a workout where every exercise is cardio (Walking, Treadmill, rowing…). Anything else is a gym session, even with some cardio in it. Long walking days are logged as one long workout, so counting them separately keeps gym figures honest.'],
  time: ['Training time', 'End minus start of each workout, as logged in Hevy. Nothing is capped or guessed. The note underneath shows the gym part only.'],
  sets: ['Working sets', 'Sets typed normal, failure or drop set. Warm-ups are kept in the history but never counted as working sets, volume or records.'],
  volume: ['Volume', 'Load × reps over working sets of weighted exercises: bar, dumbbells, machine stack or added weight. Bodyweight isn\'t included, so push-ups add reps but no volume. Cardio, holds and carries have their own totals. Sets that look like typos (e.g. 12 kg × 129) are left out and listed on the System page.'],
  records: ['Records', 'Tracked per exercise and category (estimated 1RM, heaviest weight, rep record at a weight, best set, session volume, and more). A record must beat everything before it. The first session of an exercise sets the baseline. Records from an exercise\'s first three sessions, or less than 1% better, are counted separately as early or small.'],
  change: ['Compared with what?', 'Each figure is compared with the same length of time immediately before the chosen period. Changes are only shown when both periods have at least 2 gym sessions; otherwise you\'ll see why instead of a misleading percentage.'],
  strength: ['Strength change', 'For every exercise with an estimated 1RM in both periods: best e1RM now ÷ best e1RM before − 1. The headline is the median of those changes and needs at least 3 exercises. e1RM uses the Epley formula, load × (1 + reps ÷ 30), on working sets of 1 to 12 reps. It\'s an estimate, not a measured max.'],
  muscles: ['Muscle credit', 'Each working set credits 1 set to the exercise\'s primary muscle (from Hevy) and ½ set to each secondary muscle. Volume is credited the same way. It shows where effort goes; it isn\'t physiology. Cardio, full-body and "other" exercises aren\'t drawn on the body.'],
  less: ['Trained less than usual', 'A muscle that averaged at least 2 credited sets a week over the 12 weeks before this period, and is below half that rate now. A description of your own pattern, not advice.'],
  timeline: ['The timeline', 'Working sets per week across your whole history, with cardio distance per week below and a dot for each week with a meaningful record. Drag across it to choose any period; everything on the page follows.'],
  pause: ['On a break', 'After 14 days without a gym session, the recent windows would show almost nothing. So the page leads with the break and shows the four weeks that ended with your last gym session, compared with the four weeks before them.'],
  plateau: ['Progress status', 'Based on the exercise\'s last 8 sessions with an estimated 1RM (6 needed). Improving or declining: the e1RM trend is at least 1% a month up or down. Otherwise flat, and then: stalled if there has been no new best for 6+ sessions and volume and reps aren\'t rising; productive plateau if volume or reps are up at least 5%; stable if a best was set recently. Every status lists its reasons.'],
  momentum: ['Momentum', 'Judged as of your latest workout, for exercises done within 90 days of it. Accelerating: improving, with the last 4 sessions rising at least 1% a month faster than the last 8 and a recent best. New: first done within 6 weeks. Maintaining: stable or productive plateau. Plateauing: stalled. Muscles compare credited sets a week over the last 4 weeks with the 8 before (±25%).'],
  order: ['Exercise order effect', 'Each session\'s best e1RM is compared with your level for that exercise at the time (a line fitted through its other sessions within ±8 weeks), so progress over time can\'t pose as an order effect. Sessions where it was exercise 1–2 are compared with sessions where it was 4th or later; each group needs 5+. The 90% interval comes from resampling; "clear" means it excludes zero. It\'s a pattern in your history, not proof of cause.'],
  dna: ['Training DNA', 'How you train, from what you logged: rep ranges of weighted sets (1–5, 6–12, 13+), sets to failure, drop sets, multi-muscle exercises (Hevy lists secondary muscles; a stand-in for compound lifts), equipment, cardio share, and where credited sets go. Compare any two periods to see how your style changed.'],
  balance: ['Muscle balance', 'Splits of credited sets between opposing groups. Descriptive only: no "ideal ratio" is applied, because the right balance depends on your goals.'],
  compare: ['Comparing workouts', 'Totals side by side with the change from the first workout to the second. Exercises are matched by exercise; e1RM and volume changes appear where both workouts have them.'],
  forecast: ['Next-session estimate', 'An experiment, and labelled as one. The chance is simply how often you set a new best in the same situation before: how close your last session was to your best and whether the trend was rising, counted across all your exercises. It has been checked against your history, and the reliability panel shows how often its past predictions came true. "To beat your best" is the lightest set at your usual reps whose estimated 1RM beats your best.'],
  wrapped: ['Year in Training', 'Plain facts for the period you pick, worked out live from your workouts, so it is always up to date. The comparisons divide your totals by rounded public figures (an elephant is about 6 t) and are picked to give a readable number for the size of the total: animals for a month, aircraft for a year, rockets and towers for a decade. For a year or more, "at this pace" shows how long the truly impossible would take. The maths is exact; the comparisons are for fun.'],
  explorer: ['Data explorer', 'Filter your sets any way you like. Warm-ups are left out unless you pick them, and so are probable typos unless you tick the box. Totals, charts and rows all follow the same filters, and so does the export.'],
  sindex: ['Strength Index', 'A personal composite, not a universal score. Up to 8 of your most-done exercises with an estimated 1RM (8+ sessions, at most 2 per main muscle). Each one\'s level is the median of its last 3 sessions\' best e1RM. The index starts at 100 and each week moves by the geometric mean of the week-on-week change of the exercises active in both weeks (done in the last 90 days, at least 3 needed), the way a price index is chain-linked. Exercises starting, stopping or coming back never make it jump.'],
  calendar: ['The year', 'One square per day. Brightness follows working sets that day. Cardio-only days show as the faintest step.'],
};

let pop;
function close() { if (pop) { pop.remove(); pop = null; } }
addEventListener('keydown', (e) => e.key === 'Escape' && close());
addEventListener('click', (e) => { if (pop && !pop.contains(e.target) && !e.target.closest('.info')) close(); });

// A small (i) button that opens the explanation next to it.
export function info(key) {
  const [title, text] = EXPLAIN[key];
  return h('button', { type: 'button', class: 'info', 'aria-label': `How is "${title}" calculated?`, onclick: (e) => {
    e.stopPropagation();
    const same = pop && pop.dataset.key === key;
    close();
    if (same) return;
    pop = document.body.appendChild(h('div', { class: 'explain', role: 'dialog', 'aria-label': title, 'data-key': key },
      h('b', {}, title), h('p', {}, text)));
    const r = e.currentTarget.getBoundingClientRect();
    const w = Math.min(340, innerWidth - 24);
    pop.style.width = `${w}px`;
    pop.style.left = `${Math.min(Math.max(12, r.left - 12), innerWidth - w - 12)}px`;
    pop.style.top = `${r.bottom + 8 + scrollY}px`;
  } }, 'i');
}
