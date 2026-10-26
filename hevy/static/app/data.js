// Data: explore any slice of your sets (filters, totals, charts, rows) and export exactly that slice, or everything.
import { BASE, api, h, s, css, num, num1, tonnes, dateFull, MUSCLE, link, page, failed, tooltip, hideTip, fill } from './lib.js';
import { info } from './explain.js';

let root = null, exercises = [], st = null, timer = null, req = 0;
const PAGE = 100;

const DEFAULTS = { range: 'all', start: '', end: '', exercises: '', muscle: '', primary_only: '', kind: '', set_type: 'working',
  weight_min: '', weight_max: '', reps_min: '', reps_max: '', rpe_min: '', rpe_max: '', minutes_min: '', minutes_max: '',
  include_suspect: '', sort: 'date', offset: '0' };

export async function mount(el) {
  root = el;
  try { exercises = (await api('exercises')).exercises; } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const q = new URLSearchParams(location.search);
  st = Object.fromEntries(Object.entries(DEFAULTS).map(([k, v]) => [k, q.get(k) ?? v]));
  layout();
  run();
}

const qs = (extra = {}) => {
  const all = { ...st, ...extra };
  if (all.range !== 'custom') { delete all.start; delete all.end; }
  return new URLSearchParams(Object.entries(all).filter(([, v]) => v !== '' && v != null)).toString();
};

function field(label, el) { return h('label', { class: 'fld' }, h('span', {}, label), el); }
function input(key, attrs = {}) {
  const el = h('input', { value: st[key], ...attrs });
  el.addEventListener(attrs.type === 'checkbox' ? 'change' : 'input', () => {
    st[key] = attrs.type === 'checkbox' ? (el.checked ? '1' : '') : el.value;
    st.offset = '0';
    clearTimeout(timer); timer = setTimeout(run, 300);
  });
  if (attrs.type === 'checkbox') el.checked = st[key] === '1';
  return el;
}
function select(key, options) {
  const el = h('select', { class: 'select' }, options.map(([v, l]) => h('option', { value: v, selected: st[key] === v }, l)));
  el.addEventListener('change', () => { st[key] = el.value; st.offset = '0'; if (key === 'range') dates.hidden = el.value !== 'custom'; run(); });
  return el;
}
let dates, out, exportBox;

function layout() {
  dates = h('div', { class: 'fld-row', hidden: st.range !== 'custom' }, field('From', input('start', { type: 'date' })), field('To', input('end', { type: 'date' })));
  const exList = h('datalist', { id: 'ex-list' }, exercises.map((e) => h('option', { value: e.title })));
  const exInput = h('input', { list: 'ex-list', placeholder: 'Any exercise', value: exercises.find((e) => e.template_id === st.exercises)?.title || '' });
  exInput.addEventListener('change', () => {
    const m = exercises.find((e) => e.title.toLowerCase() === exInput.value.trim().toLowerCase());
    st.exercises = m ? m.template_id : ''; st.offset = '0'; run();
  });
  const muscles = Object.entries(MUSCLE).filter(([m]) => !['other', 'full_body'].includes(m));
  out = h('div', { class: 'xp-out' }, h('p', { class: 'loading' }, 'Loading…'));
  exportBox = h('div', { class: 'export' });
  root.replaceChildren(page('Data',
    h('header', { class: 'phead' }, h('h1', {}, 'Data'), h('p', {}, 'Explore every set you have logged, then export exactly what you see, or everything.', info('explorer'))),
    h('section', { class: 'filters' },
      h('div', { class: 'fld-row' },
        field('Period', select('range', [['all', 'All time'], ['1y', 'Last 12 months'], ['6m', 'Last 6 months'], ['3m', 'Last 3 months'], ['28d', 'Last 28 days'], ['custom', 'Custom dates']])),
        field('Exercise', h('span', {}, exInput, exList)),
        field('Muscle', select('muscle', [['', 'Any muscle'], ...muscles.map(([m, l]) => [m, l])])),
        field('Sessions', select('kind', [['', 'Gym and cardio'], ['gym', 'Gym sessions'], ['cardio', 'Cardio sessions']])),
        field('Set type', select('set_type', [['working', 'Working sets'], ['normal', 'Normal'], ['failure', 'Failure'], ['dropset', 'Drop sets'], ['warmup', 'Warm-ups'], ['all', 'All sets']]))),
      dates,
      h('div', { class: 'fld-row' },
        field('Weight (kg)', h('span', { class: 'mm2' }, input('weight_min', { type: 'number', step: 'any', placeholder: 'min', inputmode: 'decimal' }), input('weight_max', { type: 'number', step: 'any', placeholder: 'max', inputmode: 'decimal' }))),
        field('Reps', h('span', { class: 'mm2' }, input('reps_min', { type: 'number', placeholder: 'min', inputmode: 'numeric' }), input('reps_max', { type: 'number', placeholder: 'max', inputmode: 'numeric' }))),
        field('RPE', h('span', { class: 'mm2' }, input('rpe_min', { type: 'number', step: '0.5', placeholder: 'min' }), input('rpe_max', { type: 'number', step: '0.5', placeholder: 'max' }))),
        field('Workout length (min)', h('span', { class: 'mm2' }, input('minutes_min', { type: 'number', placeholder: 'min' }), input('minutes_max', { type: 'number', placeholder: 'max' })))),
      h('div', { class: 'fld-row checks' },
        h('label', { class: 'check' }, input('primary_only', { type: 'checkbox' }), 'Muscle must be the main one'),
        h('label', { class: 'check' }, input('include_suspect', { type: 'checkbox' }), 'Include probable typos'),
        h('button', { type: 'button', class: 'btn-s', onclick: () => { history.replaceState(null, '', location.pathname); mount(root); } }, 'Reset'))),
    out, exportBox));
  drawExport();
}

async function run() {
  const mine = ++req;
  history.replaceState(null, '', location.pathname + '?' + qs());
  let d;
  try { d = await api(`explore?${qs({ limit: PAGE })}`); } catch (e) { if (mine === req) out.replaceChildren(failed(e)); return; }
  if (mine !== req || !root) return;
  const t = d.totals;
  const stat = (l, v) => h('div', { class: 'stat' }, h('span', {}, l), h('b', {}, v));
  const sortBtn = (k, l) => h('button', { type: 'button', class: st.sort === k ? 'on' : '', onclick: () => { st.sort = k; st.offset = '0'; run(); } }, l);
  const offset = Number(d.offset);
  fill(out,
    h('div', { class: 'stats' }, stat('Sets', num(t.sets)), stat('Workouts', num(t.workouts)), stat('Exercises', num(t.exercises)), stat('Reps', num(t.reps)),
      stat('Volume', tonnes(t.volume_kg)), stat('Average load', t.avg_weight_kg == null ? '–' : `${num1(t.avg_weight_kg)} kg`),
      stat('Average reps', num1(t.avg_reps)), stat('Average RPE', t.avg_rpe == null ? '–' : `${num1(t.avg_rpe)} (${num(t.rpe_sets)} sets)`),
      stat('Best e1RM', t.best_e1rm_kg == null ? '–' : `${num1(t.best_e1rm_kg)} kg`)),
    t.sets ? h('div', { class: 'xp-charts' },
      chartCard('Sets per week', bars(d.weekly.map((w) => ({ x: w.start, v: w.sets, tip: [[`Week of ${dateFull(w.start)}`, `${num(w.sets)} sets`], ['Volume', tonnes(w.volume_kg)]] })), true)),
      chartCard('Reps per set', bars(d.reps_histogram.map((b) => ({ x: b.reps >= 30 ? '30+' : String(b.reps), v: b.sets, tip: [[`${b.reps >= 30 ? '30+' : b.reps} reps`, `${num(b.sets)} sets`]] })))),
      d.weight_histogram.length ? chartCard(`Load (${num1(d.weight_step)} kg bands)`, bars(d.weight_histogram.map((b) => ({ x: `${num1(b.from)}`, v: b.sets, tip: [[`${num1(b.from)}–${num1(b.to)} kg`, `${num(b.sets)} sets`]] })))) : null,
      d.scatter.length ? chartCard(`Load vs reps${d.scatter_thinned ? ' (evenly thinned)' : ''}`, scatter(d.scatter)) : null) : h('p', { class: 'lede' }, 'No sets match these filters.'),
    t.sets ? h('section', { class: 'block' },
      h('div', { class: 'head' }, h('h2', {}, 'Sets'), h('div', { class: 'ranges' }, sortBtn('date', 'Newest'), sortBtn('weight', 'Heaviest'), sortBtn('reps', 'Most reps'),
        sortBtn('e1rm', 'Best e1RM'), sortBtn('rpe', 'Highest RPE'))),
      h('div', { class: 'xtbl' }, h('div', { class: 'xtr th' }, ['Date', 'Exercise', 'Type', 'Load', 'Reps', 'RPE', 'e1RM', 'Workout'].map((c) => h('span', {}, c))),
        d.rows.map((r) => h('div', { class: `xtr ${r.suspect ? 'sus' : ''}`, title: r.suspect || '' }, h('span', {}, dateFull(r.local_date)),
          link(`/exercises/${encodeURIComponent(r.template_id)}`, { class: 'ell' }, r.exercise), h('span', {}, r.set_type),
          h('span', {}, r.weight_kg != null ? `${num1(r.weight_kg)} kg` : r.distance_m ? `${num(r.distance_m)} m` : r.duration_s ? `${num(r.duration_s)} s` : '–'),
          h('span', {}, r.reps != null ? num(r.reps) : '–'), h('span', {}, r.rpe != null ? num1(r.rpe) : ''), h('span', {}, r.e1rm_kg != null ? num1(r.e1rm_kg) : ''),
          link(`/sessions/${encodeURIComponent(r.workout_id)}`, { class: 'ell' }, `#${r.position} · ${r.workout || 'Workout'}`)))),
      h('div', { class: 'pager' },
        h('button', { type: 'button', class: 'btn-s', disabled: offset === 0, onclick: () => { st.offset = String(Math.max(0, offset - PAGE)); run(); } }, '← Previous'),
        h('span', {}, `${num(offset + 1)}–${num(Math.min(t.sets, offset + PAGE))} of ${num(t.sets)}`),
        h('button', { type: 'button', class: 'btn-s', disabled: offset + PAGE >= t.sets, onclick: () => { st.offset = String(offset + PAGE); run(); } }, 'Next →'))) : null);
  drawExport();
}

function chartCard(title, svg) { return h('div', { class: 'xp-card' }, h('p', { class: 'kicker' }, title), svg); }

function bars(items, time = false) {
  const W = 520, H = 150, pad = 18, n = Math.max(1, items.length), bw = W / n, max = Math.max(1, ...items.map((i) => i.v));
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'xp-svg', preserveAspectRatio: 'none', role: 'img' });
  items.forEach((it, i) => {
    const hh = (H - pad) * it.v / max;
    svg.append(s('rect', { x: i * bw + (n > 60 ? 0 : 1), y: H - pad - hh, width: Math.max(1, bw - (n > 60 ? 0 : 2)), height: Math.max(it.v ? 1 : 0, hh), rx: n > 60 ? 0 : 2,
      class: 'xb', onmousemove: (e) => tooltip(e, it.tip[0][0], [['', it.tip[0][1]], ...it.tip.slice(1)]), onmouseleave: hideTip }));
  });
  const ticks = [0, Math.floor(n / 2), n - 1];
  for (const i of [...new Set(ticks)]) if (items[i]) svg.append(s('text', { x: i * bw + bw / 2, y: H - 4, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle', class: 'xl' },
    time ? items[i].x.slice(0, 7) : items[i].x));
  return svg;
}

function scatter(pts) {
  const W = 520, H = 200, pl = 34, pb = 18;
  const mw = Math.max(...pts.map((p) => p.w)), mr = Math.min(40, Math.max(...pts.map((p) => p.r)));
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'xp-svg', role: 'img', 'aria-label': 'Load against reps for each set' });
  svg.append(s('text', { x: pl, y: H - 3, class: 'xl' }, '0 reps'), s('text', { x: W, y: H - 3, 'text-anchor': 'end', class: 'xl' }, `${num(mr)}+ reps`),
    s('text', { x: 0, y: 10, class: 'xl' }, `${num(mw)} kg`));
  for (const p of pts) svg.append(s('circle', { cx: pl + (Math.min(p.r, mr) / mr) * (W - pl - 6), cy: (H - pb) - (p.w / mw) * (H - pb - 14), r: 2.4, class: 'xd',
    onmousemove: (e) => tooltip(e, dateFull(p.d), [['Load', `${num1(p.w)} kg`], ['Reps', num(p.r)]]), onmouseleave: hideTip }));
  return svg;
}

const exp = { format: 'xlsx', datasets: new Set(['sets', 'workouts', 'exercises', 'records']), scope: 'filtered' };
function drawExport() {
  if (!exportBox) return;
  const dsets = [['sets', 'Sets'], ['workouts', 'Workouts'], ['exercises', 'Exercises'], ['records', 'Records'], ['days', 'Days'], ['raw', 'Raw Hevy data']];
  const base = exp.scope === 'filtered' ? (() => {
    const p = new URLSearchParams(qs());
    const keep = new URLSearchParams();
    for (const k of ['range', 'start', 'end', 'muscle']) if (p.get(k)) keep.set(k, p.get(k));
    if (p.get('exercises')) keep.set('exercise', p.get('exercises'));
    return keep;
  })() : new URLSearchParams({ range: 'all' });
  base.set('format', exp.format);
  base.set('datasets', [...exp.datasets].join(','));
  const href = `${BASE}/api/export?${base}`;
  exportBox.replaceChildren(h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Export'), h('span', {}, 'Your local copy of the Hevy history and the derived figures')),
    h('div', { class: 'fld-row' },
      field('Format', (() => { const el = h('select', { class: 'select' }, [['xlsx', 'Excel (.xlsx)'], ['csv', 'CSV (ZIP if several)'], ['json', 'JSON']].map(([v, l]) => h('option', { value: v, selected: exp.format === v }, l)));
        el.addEventListener('change', () => { exp.format = el.value; drawExport(); }); return el; })()),
      field('What', (() => { const el = h('select', { class: 'select' }, [['filtered', 'Period, exercise and muscle above'], ['all', 'Everything, all time']].map(([v, l]) => h('option', { value: v, selected: exp.scope === v }, l)));
        el.addEventListener('change', () => { exp.scope = el.value; drawExport(); }); return el; })())),
    h('div', { class: 'fld-row checks' }, dsets.map(([k, l]) => h('label', { class: 'check' },
      (() => { const c = h('input', { type: 'checkbox' }); c.checked = exp.datasets.has(k); c.addEventListener('change', () => { c.checked ? exp.datasets.add(k) : exp.datasets.delete(k); drawExport(); }); return c; })(), l))),
    exp.datasets.size ? h('a', { class: 'btn-s dl', href, download: '' }, `Download ${exp.format.toUpperCase()}`) : h('p', { class: 'note' }, 'Pick at least one dataset.'),
    h('p', { class: 'note' }, 'Set-level filters (weight, reps, RPE, set type) shape the explorer only; exports keep every set in the chosen period so nothing is silently missing. Each file includes an About section with the filters and rules version.')));
}

export function unmount() { root = null; clearTimeout(timer); hideTip(); }
