// One exercise in depth: status and why, lifetime figures, the session-by-session chart, order effect, records, sessions.
import { api, h, css, num, num1, tonnes, km, hours, signed, dateShort, dateFull, MUSCLE, RECORD, STATUS, MOMENTUM, chip, link, page, crumb,
  failed, go } from './lib.js';
import { strips } from './chart.js';
import { info } from './explain.js';

let root = null, redraw = null;
const onResize = () => redraw?.();

const LAYERS = {
  load_reps: [
    ['best_e1rm', 'Estimated 1RM', (v) => `${num1(v)} kg`, '#a3cf1c'], ['top_weight', 'Top weight', (v) => `${num1(v)} kg`, '#7a9e14'],
    ['volume_kg', 'Session volume', (v) => tonnes(v), '#7a9e14', { bars: true, zero: true }], ['working_sets', 'Working sets', num, '#5b6472', { bars: true, zero: true }],
    ['avg_rpe', 'Average RPE', num1, '#d95926'], ['position', 'Position in workout', (v) => `#${num(v)}`, '#3987e5', { invert: true }],
  ],
  cardio: [['distance_m', 'Distance', km, '#3987e5', { bars: true, zero: true }], ['duration_s', 'Time', (v) => hours(v), '#3987e5', { bars: true, zero: true }]],
  reps: [['max_reps', 'Most reps in a set', num, '#a3cf1c'], ['reps', 'Total reps', num, '#7a9e14', { bars: true, zero: true }], ['working_sets', 'Working sets', num, '#5b6472', { bars: true, zero: true }]],
  time: [['duration_s', 'Total time', (v) => `${num(v)} s`, '#a3cf1c', { bars: true, zero: true }], ['working_sets', 'Working sets', num, '#5b6472', { bars: true, zero: true }]],
};
const DEFAULT_ON = { best_e1rm: 1, volume_kg: 1, distance_m: 1, duration_s: 1, max_reps: 1, reps: 1, top_weight: 0, working_sets: 0, avg_rpe: 0, position: 0 };

export async function mount(el, templateId) {
  root = el;
  let d;
  try { d = await api(`exercises/${encodeURIComponent(templateId)}`); } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const t = d.template, lt = d.lifetime;
  if (!lt) { root.replaceChildren(page(t.title, h('h1', {}, t.title), h('p', { class: 'lede' }, 'Never done yet.'))); return; }
  const layers = (LAYERS[t.measure] || LAYERS.load_reps).map(([key, label, fmt, color, opts = {}]) => ({ key, label, fmt, color, ...opts }));
  const on = new Set(layers.filter((l) => DEFAULT_ON[l.key]).map((l) => l.key));
  const series = d.series.map((s) => ({ ...s, t: Date.parse(s.date + 'T12:00:00'), title: s.workout_title,
    note: s.records.length ? `Records: ${s.records.map((r) => RECORD[r] || r).join(', ')}` : null }));
  const chartBox = h('div', { class: 'chart' });
  const toggles = h('div', { class: 'ranges wrap' });
  const drawChart = () => {
    toggles.replaceChildren(...layers.filter((l) => series.some((p) => p[l.key] != null)).map((l) => h('button', { type: 'button', class: on.has(l.key) ? 'on' : '',
      'aria-pressed': String(on.has(l.key)), onclick: () => { on.has(l.key) ? on.size > 1 && on.delete(l.key) : on.add(l.key); drawChart(); } }, l.label)));
    redraw = strips(chartBox, { points: series, layers: layers.filter((l) => on.has(l.key)),
      markers: (p, key) => key === 'best_e1rm' && p.records.includes('e1rm'), onPick: (p) => go(`/sessions/${encodeURIComponent(p.workout_id)}`) });
  };

  const stat = (label, value, sub) => h('div', { class: 'stat' }, h('span', {}, label), h('b', {}, value), sub ? h('small', {}, sub) : null);
  const stats = [
    stat('Sessions', num(lt.sessions), `${dateShort(lt.first)} – ${dateFull(lt.last)}`),
    lt.best_e1rm ? stat('Best estimated 1RM', `${num1(lt.best_e1rm.kg)} kg`, `${num1(lt.best_e1rm.weight_kg)} kg × ${num(lt.best_e1rm.reps)} · ${dateFull(lt.best_e1rm.date)}`) : null,
    lt.heaviest ? stat('Heaviest', `${num1(lt.heaviest.weight_kg)} kg`, `${lt.heaviest.reps ? `× ${num(lt.heaviest.reps)} · ` : ''}${dateFull(lt.heaviest.date)}`) : null,
    lt.best_session_volume ? stat('Best session volume', tonnes(lt.best_session_volume.kg), dateFull(lt.best_session_volume.date)) : null,
    lt.volume_kg ? stat('Lifetime volume', tonnes(lt.volume_kg), `${num(lt.working_sets)} sets · ${num(lt.reps)} reps`) : stat('Working sets', num(lt.working_sets), lt.reps ? `${num(lt.reps)} reps` : null),
    lt.distance_m ? stat('Lifetime distance', km(lt.distance_m), hours(lt.duration_s)) : null,
    lt.max_reps && t.measure !== 'load_reps' ? stat('Most reps in a set', num(lt.max_reps)) : null,
    lt.longest_hold_s && t.measure === 'time' ? stat('Longest hold', `${num(lt.longest_hold_s)} s`) : null,
    stat('Records', num(lt.meaningful_records), `${lt.records - lt.meaningful_records} more early or small`),
    stat('How often', `${num1(lt.sessions_per_week)}/wk`, 'while you were doing it'),
    stat('Usually', `exercise #${lt.typical_position}`, `${num(lt.positions['1st'])}× first`),
    lt.rpe_sets ? stat('Average RPE', num1(lt.avg_rpe), `${num(lt.rpe_sets)} sets with RPE`) : null,
  ];

  const p = d.plateau;
  const status = p ? h('section', { class: `status-card tone-${STATUS[p.status][1]}` },
    h('div', { class: 'sc-head' }, h('p', { class: 'kicker' }, 'Progress status', info('plateau')),
      h('h2', {}, STATUS[p.status][0]), d.momentum ? h('p', { class: 'muted' }, 'Momentum: ', chip(MOMENTUM[d.momentum.momentum]), info('momentum')) : null),
    h('ul', { class: 'reasons' }, p.reasons.map((r) => h('li', {}, r))),
    d.trend?.available ? h('p', { class: 'muted' }, `Trend over the last ${d.trend.sessions} sessions: ${signed(d.trend.pct_per_month)} a month (${d.trend.kg_per_month > 0 ? '+' : ''}${num1(d.trend.kg_per_month)} kg).`) : null)
    : h('section', { class: 'status-card tone-none' }, h('p', { class: 'kicker' }, 'Progress status'),
      h('p', { class: 'muted' }, 'Progress status uses estimated 1RM, which only load × reps exercises have.'));

  const o = d.order_effect;
  const order = o ? h('section', { class: 'card-s' }, h('div', { class: 'head' }, h('h2', {}, 'Does order matter?', info('order'))),
    o.available ? [
      h('div', { class: 'oe' },
        h('div', {}, h('span', {}, `As ${o.early_label}`), h('b', {}, `${num1(o.early_avg_e1rm)} kg`), h('small', {}, `average e1RM · ${o.early_n} sessions`)),
        h('div', {}, h('span', {}, `As ${o.late_label}`), h('b', {}, `${num1(o.late_avg_e1rm)} kg`), h('small', {}, `average e1RM · ${o.late_n} sessions`)),
        h('div', { class: o.clear ? 'clear' : '' }, h('span', {}, 'Adjusted for progress'), h('b', {}, signed(o.effect_pct)),
          h('small', {}, `90% interval ${signed(o.interval_90[0])} to ${signed(o.interval_90[1])} · ${o.clear ? 'clear' : 'unclear'}`))),
      h('p', { class: 'lede' }, o.summary), h('p', { class: 'note' }, o.caution)]
      : h('p', { class: 'lede' }, `Not enough data yet: ${o.reason}.`)) : null;

  const recs = d.records.filter((r) => r.meaningful).slice(0, 12);
  const records = h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'Records', info('records')), h('span', {}, `${lt.meaningful_records} meaningful`)),
    recs.length ? h('ul', { class: 'recs' }, recs.map((r) => h('li', {}, h('span', { class: 'd' }, dateShort(r.local_date)),
      h('span', {}, RECORD[r.category] || r.category, h('span', { class: 'c' }, r.improvement_pct != null && r.unit === 'kg' ? `${signed(r.improvement_pct)} on the previous best` : `was ${num1(r.previous)}`)),
      link(`/sessions/${encodeURIComponent(r.workout_id)}`, { class: 'x' }, r.detail.replace(/ \(was.*\)/, ''))))) : h('p', { class: 'lede' }, 'No meaningful records yet.'));

  const nextBox = h('section', { class: 'next-card' }, h('p', { class: 'loading' }, 'Working out the next-session estimate…'));
  if (t.measure === 'load_reps') loadNext(nextBox, templateId);

  const recent = [...d.series].reverse().slice(0, 15);
  const sessions = h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'Recent sessions'), h('span', {}, `${lt.sessions} in total`)),
    h('div', { class: 'tbl' }, h('div', { class: 'tr th' }, h('span', {}, 'Date'), h('span', {}, 'Workout'), h('span', {}, 'Best'), h('span', {}, 'Sets'), h('span', {}, 'Volume'), h('span', {}, '#')),
      recent.map((s) => link(`/sessions/${encodeURIComponent(s.workout_id)}`, { class: 'tr' }, h('span', {}, dateFull(s.date)), h('span', { class: 'ell' }, s.workout_title || 'Workout'),
        h('span', {}, s.best_e1rm ? `${num1(s.top_weight)} × ${num(s.top_reps)}` : s.distance_m ? km(s.distance_m) : s.max_reps ? `${num(s.max_reps)} reps` : '–'),
        h('span', {}, num(s.working_sets)), h('span', {}, s.volume_kg ? tonnes(s.volume_kg) : '–'), h('span', {}, `${s.position}/${s.of}`)))));

  root.replaceChildren(page(t.title,
    crumb(link('/exercises', {}, 'Exercises'), h('span', {}, t.title)),
    h('header', { class: 'phead' }, h('h1', {}, t.title),
      h('p', { class: 'tags' }, t.primary_muscle && t.primary_muscle !== 'other' ? link(`/muscles/${t.primary_muscle}`, { class: 'tag strong' }, MUSCLE[t.primary_muscle] || t.primary_muscle) : null,
        (t.secondary_muscles || []).map((m) => link(`/muscles/${m}`, { class: 'tag' }, MUSCLE[m] || m)), t.equipment ? h('span', { class: 'tag ghost' }, t.equipment.replace('_', ' ')) : null,
        t.is_custom ? h('span', { class: 'tag ghost' }, 'custom') : null, t.known_to_hevy ? null : h('span', { class: 'tag ghost' }, 'no longer in Hevy'))),
    h('div', { class: 'ex-top' }, status, h('div', { class: 'stats' }, stats)),
    t.measure === 'load_reps' ? nextBox : null,
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Session by session'), h('span', {}, 'Bright dots: new e1RM records · click a point to open the workout')),
      toggles, chartBox),
    h('div', { class: 'two' }, order, records), sessions));
  addEventListener('resize', onResize);
  drawChart();
}

async function loadNext(box, templateId) {
  let n, bt;
  try { [n, bt] = await Promise.all([api(`exercises/${encodeURIComponent(templateId)}/next`), api('forecast/backtest')]); }
  catch (e) { box.replaceChildren(h('p', { class: 'lede' }, `Couldn't estimate (${e.message}).`)); return; }
  if (!root) return;
  if (!n.available) {
    box.replaceChildren(h('p', { class: 'kicker' }, 'Next session · experimental', info('forecast')), h('p', { class: 'lede' }, `No estimate yet: ${n.reason}.`));
    return;
  }
  const track = bt.available ? h('details', { class: 'track' }, h('summary', {}, `How reliable is this? Checked against ${num(bt.n)} past sessions`),
    h('p', { class: 'lede' }, `Predicting each exercise from the others only: Brier score ${bt.brier} vs ${bt.brier_baseline} for always guessing ${bt.base_rate_pct}%. `,
      bt.better_than_baseline ? 'Slightly better than guessing the average: well calibrated, but only modestly informative.' : 'No better than guessing the average, so treat it as a rough guide.'),
    h('div', { class: 'calib' }, bt.bins.map((x) => h('div', {}, h('span', {}, `Said ${x.range}`), h('b', {}, `${num1(x.happened_pct)}%`), h('small', {}, `happened · ${num(x.n)} cases`))))) : null;
  box.replaceChildren(
    h('div', { class: 'nc-head' }, h('p', { class: 'kicker' }, 'Next session · experimental estimate', info('forecast')), h('span', { class: `conf ${n.confidence}` }, `${n.confidence} confidence`)),
    h('div', { class: 'nc-grid' },
      h('div', { class: 'nc-big' }, h('b', {}, `${num(n.chance_new_best_pct)}%`), h('span', {}, 'chance of a new best e1RM')),
      h('div', {}, h('span', {}, 'To beat your best'), h('b', {}, `${num1(n.to_beat_best.weight_kg)} kg × ${num(n.to_beat_best.reps)}`),
        h('small', {}, `best e1RM ${num1(n.best_e1rm)} kg · loads in ${num1(n.to_beat_best.load_step_kg)} kg steps`)),
      h('div', {}, h('span', {}, 'The trend points to'), h('b', {}, `${num1(n.trend_points_to.weight_kg)} kg × ${num(n.trend_points_to.reps)}`),
        h('small', {}, `about ${num1(n.trend_points_to.e1rm)} kg e1RM · trend ${signed(n.trend_kg_per_month)}`))),
    h('ul', { class: 'reasons' }, n.reasons.map((r) => h('li', {}, r))), track);
}

export function unmount() { removeEventListener('resize', onResize); root = null; redraw = null; }
