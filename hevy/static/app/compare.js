// Two workouts side by side: totals, matched exercises, muscles.
import { api, h, css, num, num1, tonnes, hours, km, signed, dateFull, MUSCLE, link, page, crumb, failed, go } from './lib.js';
import { info } from './explain.js';

let root = null;

export async function mount(el) {
  root = el;
  const q = new URLSearchParams(location.search);
  const a = q.get('a'), b = q.get('b');
  if (!a || !b) {
    root.replaceChildren(page('Compare', h('header', { class: 'phead' }, h('h1', {}, 'Compare two workouts'),
      h('p', {}, 'Open any session and press Compare next to a similar one, or pick two from Sessions.')), link('/sessions', { class: 'btn-s' }, 'Go to sessions')));
    return;
  }
  let d;
  try { d = await api(`compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`); } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const A = d.a, B = d.b;
  const ch = (v) => (v == null ? h('span', { class: 'na' }, '–') : h('span', { class: v > 0 ? 'up' : v < 0 ? 'down' : '' }, signed(v)));
  const row = (label, fa, fb, key, fmt) => h('div', { class: 'cmp-row' }, h('span', {}, label), h('b', {}, fmt(fa)), h('b', {}, fmt(fb)), ch(d.change[key]));
  const head = (w, tag) => link(`/sessions/${encodeURIComponent(w.workout_id)}`, { class: 'cmp-h' }, h('em', {}, tag), h('b', {}, w.title || 'Workout'), h('span', {}, dateFull(w.local_date)));
  const maxM = Math.max(1, ...d.muscles.map((m) => Math.max(m.a, m.b)));
  const bar = (v, cls) => css(h('span', { class: `mb ${cls}` }), { width: `${v / maxM * 100}%` });
  const exCell = (x) => (!x ? h('span', { class: 'na' }, 'not done') : h('span', {},
    x.best_e1rm_kg ? `e1RM ${num1(x.best_e1rm_kg)} · ${num1(x.top_weight_kg)}×${num(x.top_weight_reps)}` : x.total_distance_m ? km(x.total_distance_m) : `${num(x.reps)} reps`,
    h('small', {}, `${num(x.working_sets)} sets${x.volume_kg ? ` · ${tonnes(x.volume_kg)}` : ''} · #${x.position + 1}`)));

  root.replaceChildren(page('Compare',
    crumb(link('/sessions', {}, 'Sessions'), h('span', {}, 'Compare')),
    h('header', { class: 'phead' }, h('h1', {}, 'Two workouts, side by side', info('compare')),
      h('p', {}, `${num(d.shared_exercises)} exercise${d.shared_exercises === 1 ? '' : 's'} in common. Changes read from the first to the second.`),
      h('button', { type: 'button', class: 'btn-s', onclick: () => go(`/compare?a=${encodeURIComponent(b)}&b=${encodeURIComponent(a)}`) }, 'Swap')),
    h('section', { class: 'cmp' },
      h('div', { class: 'cmp-row cmp-top' }, h('span'), head(A, 'First'), head(B, 'Second'), h('span', {}, 'Change')),
      row('Duration', A.duration_s, B.duration_s, 'duration_s', hours),
      row('Working sets', A.working_sets, B.working_sets, 'working_sets', num),
      row('Reps', A.reps, B.reps, 'reps', num),
      row('Volume', A.volume_kg, B.volume_kg, 'volume_kg', tonnes),
      A.distance_m || B.distance_m ? row('Cardio distance', A.distance_m, B.distance_m, 'distance_m', km) : null,
      row('Records', A.meaningful_prs, B.meaningful_prs, 'meaningful_prs', num),
      A.avg_rpe || B.avg_rpe ? row('Average RPE', A.avg_rpe, B.avg_rpe, 'avg_rpe', num1) : null),
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Exercise by exercise'), h('span', {}, 'Matched by exercise')),
      h('div', { class: 'cmp' }, d.exercises.map((e) => h('div', { class: 'cmp-row' },
        link(`/exercises/${encodeURIComponent(e.template_id)}`, {}, e.title), exCell(e.a), exCell(e.b),
        e.e1rm_change_pct != null ? h('span', {}, ch(e.e1rm_change_pct), h('small', {}, 'e1RM')) : e.volume_change_pct != null ? h('span', {}, ch(e.volume_change_pct), h('small', {}, 'volume')) : h('span', { class: 'na' }, '–'))))),
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Muscles', info('muscles')), h('span', {}, h('i', { class: 'k a' }), 'first ', h('i', { class: 'k b' }), 'second')),
      h('div', { class: 'mcmp' }, d.muscles.filter((m) => !['cardio', 'other', 'full_body'].includes(m.muscle)).map((m) => h('div', { class: 'mrow' },
        link(`/muscles/${m.muscle}`, {}, MUSCLE[m.muscle] || m.muscle), h('span', { class: 'mbars' }, bar(m.a, 'a'), bar(m.b, 'b')), h('em', {}, `${num1(m.a)} → ${num1(m.b)}`)))))));
}

export function unmount() { root = null; }
