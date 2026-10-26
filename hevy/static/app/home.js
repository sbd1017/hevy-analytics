// The homepage: "what is happening with my training?" One period drives everything on the page; it is chosen with
// the tabs or by dragging across the whole-history timeline, and kept in the URL (?range= or ?start=&end=).
import { nextUp } from './coach.js';
import { api, h, s, css, num, num1, tonnes, hours, km, signed, dateLong, dateShort, dateFull, addDays, MUSCLE, RECORD,
  tooltip, hideTip, link, go as goTo } from './lib.js';
import { bodyMap } from './body.js';
import { ensureFigure, sexSwitch } from './figure.js';
import { info } from './explain.js';

const LIME = ['#1d2410', '#2c3a0f', '#41580f', '#5b7a12', '#7a9e14', '#a3cf1c', '#d7ff3a'];   // sequential, dark -> bright
const RANGES = [['7d', '7 days'], ['28d', '28 days'], ['3m', '3 months'], ['6m', '6 months'], ['1y', 'Year'], ['all', 'All time']];
const BREAK_DAYS = 14;

let root = null, state = null, selectedMuscle = null, timer = null, gen = 0;
const cache = new Map();
const once = {};

function readState() {
  const q = new URLSearchParams(location.search);
  if (q.get('start') && q.get('end')) return { range: 'custom', start: q.get('start'), end: q.get('end') };
  return { range: RANGES.some(([k]) => k === q.get('range')) ? q.get('range') : '28d' };
}

function writeState() {
  const q = state.range === 'custom' ? `?start=${state.start}&end=${state.end}` : state.range === '28d' ? '' : `?range=${state.range}`;
  history.replaceState(null, '', location.pathname + q);
}

const key = (st) => (st.range === 'custom' ? `${st.start}_${st.end}` : st.range);

// Summary for the state, with break handling: after 14+ days without a gym session, the short preset windows lead
// with the break and show the four weeks that ended with the last gym session (see EXPLAIN.pause).
async function load(st) {
  await ensureFigure();
  const k = key(st);
  if (!cache.has(k)) cache.set(k, (async () => {
    const q = st.range === 'custom' ? `range=custom&start=${st.start}&end=${st.end}` : `range=${st.range}`;
    const sum = await api(`summary?${q}`);
    let hero = sum, pause = null;
    if (st.range !== 'custom' && ['7d', '28d'].includes(st.range) && sum.days_since_last_gym_session >= BREAK_DAYS) {
      const end = addDays(sum.today, -sum.days_since_last_gym_session), start = addDays(end, -27);
      hero = await api(`summary?range=custom&start=${start}&end=${end}`);
      pause = { days: sum.days_since_last_gym_session, start, end };
    }
    const p = hero.period;
    const exercises = (await api(`exercises?range=custom&start=${p.start}&end=${p.end}`)).exercises;
    return { sum, hero, pause, exercises };
  })());
  return cache.get(k);
}

// ---------------------------------------------------------------- pieces

const kicker = (text, explain) => h('p', { class: 'kicker' }, text, explain ? info(explain) : null);

function statusStrip(sum) {
  const lw = sum.last_workout, st = sum.streaks.any;
  if (!lw) return h('div', { class: 'status' }, 'No workouts yet. They appear here after the first sync.');
  return h('div', { class: 'status' },
    h('span', {}, h('i', { class: `dot${sum.days_since_last_workout > 7 ? ' idle' : ''}` }), 'Last workout ',
      link(`/sessions/${encodeURIComponent(lw.workout_id)}`, { class: 'strong-link' }, dateLong(lw.local_date)),
      lw.title ? ` · ${lw.title}` : ''),
    h('span', {}, 'Streak ', h('b', {}, `${st.current_weeks} week${st.current_weeks === 1 ? '' : 's'}`)),
    st.longest_weeks ? h('span', {}, 'Longest ', h('b', {}, `${st.longest_weeks} weeks`), ` (${dateShort(st.longest_from)} to ${dateFull(st.longest_to)})`) : null);
}

function tabs() {
  return h('div', { class: 'ranges', role: 'tablist', 'aria-label': 'Period' },
    RANGES.map(([k, label]) => h('button', { type: 'button', role: 'tab', 'aria-selected': String(state.range === k), class: state.range === k ? 'on' : '',
      onclick: () => go({ range: k }) }, label)),
    state.range === 'custom' ? h('button', { type: 'button', role: 'tab', 'aria-selected': 'true', class: 'on custom', onclick: () => go({ range: '28d' }) },
      `${dateShort(state.start)} – ${dateFull(state.end)} ✕`) : null);
}

function heading(sum, hero, pause) {
  if (pause) {
    return h('div', { class: 'pause' }, h('div', { class: 'n' }, pause.days),
      h('div', {}, h('h1', {}, 'days since your last gym session.'),
        h('p', {}, `Below: the four weeks that ended with it (${dateShort(pause.start)} to ${dateFull(pause.end)}), compared with the four weeks before. `,
          'Pick any other period above, or drag across the timeline.', info('pause'))));
  }
  const p = hero.period;
  const title = p.key === 'custom' ? `${dateShort(p.start)}${p.start.slice(0, 4) !== p.end.slice(0, 4) ? ` ${p.start.slice(0, 4)}` : ''} – ${dateFull(p.end)}` : p.label;
  return h('div', { class: 'headline' }, h('h1', {}, title),
    h('p', {}, p.prev_start ? `${dateFull(p.start)} to ${dateFull(p.end)} · compared with the ${p.days} days before` : `${dateFull(p.start)} to ${dateFull(p.end)}`,
      p.prev_start ? info('change') : null));
}

function board(hero) {
  const t = hero.totals, c = hero.change;
  const delta = (k) => {
    if (!c.available || c[k] == null) return null;
    const v = c[k];
    return h('span', { class: `delta ${v > 0 ? 'up' : v < 0 ? 'down' : ''}`, title: 'Compared with the period before' }, signed(v));
  };
  const split = (str) => { const i = str.lastIndexOf(' '); return i > 0 ? [str.slice(0, i), str.slice(i + 1)] : [str, null]; };
  const cell = (label, explain, [value, unit], k, note) => h('div', { class: 'cell' }, kicker(label, explain),
    h('div', { class: 'v' }, value, unit ? h('small', {}, unit) : null), delta(k), note ? h('p', { class: 'note' }, note) : null);
  return [
    h('div', { class: 'board' },
      cell('Gym sessions', 'sessions', [num(t.gym_sessions)], 'gym_sessions', t.cardio_sessions ? `+ ${t.cardio_sessions} cardio` : 'no cardio sessions'),
      cell('Training time', 'time', split(hours(t.duration_s)), 'duration_s', `${hours(t.gym_duration_s)} in the gym`),
      cell('Working sets', 'sets', [num(t.working_sets)], 'working_sets', `${num(t.reps)} reps`),
      cell('Volume', 'volume', split(tonnes(t.volume_kg)), 'volume_kg', t.distance_m ? `${km(t.distance_m)} cardio` : null),
      cell('Records', 'records', [num(t.meaningful_prs)], 'meaningful_prs', t.prs > t.meaningful_prs ? `+ ${t.prs - t.meaningful_prs} early or small` : null)),
    c.available ? null : h('p', { class: 'note wide' }, hero.period.prev_start ? `No % changes: ${c.reason}.` : 'All time: nothing to compare with.'),
  ];
}

// ---------------------------------------------------------------- the timeline (whole history; drag to choose)

function timeline(hero) {
  const weeks = once.weeks, recs = once.recsByWeek, n = weeks.length;
  const box = h('div', { class: 'scope' });
  const draw = () => {
    const W = Math.max(300, box.clientWidth - 24), H = W < 640 ? 210 : 260;
    const top = 16, mainB = H - 74, cTop = H - 58, cB = H - 24, bw = W / n, x = (i) => (i + 0.5) * bw;
    const maxS = Math.max(10, ...weeks.map((w) => w.working_sets)), maxC = Math.max(1, ...weeks.map((w) => w.distance_m));
    const y = (v) => mainB - (mainB - top) * v / maxS;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Working sets per week across your whole history; drag to choose a period' });
    svg.append(s('defs', {}, s('linearGradient', { id: 'tl-g', x1: 0, y1: 0, x2: 0, y2: 1 },
      s('stop', { offset: '0', 'stop-color': '#a3cf1c', 'stop-opacity': '.32' }), s('stop', { offset: '1', 'stop-color': '#a3cf1c', 'stop-opacity': '0' }))));
    svg.append(s('line', { x1: 0, x2: W, y1: y(maxS / 2), y2: y(maxS / 2), class: 'grid' }),
      s('text', { x: W - 2, y: y(maxS / 2) - 4, 'text-anchor': 'end', class: 'lab' }, `${Math.round(maxS / 2)} sets/wk`));
    weeks.forEach((w, i) => {
      if (i && w.start.slice(5) <= '01-07') svg.append(s('line', { x1: i * bw, x2: i * bw, y1: 4, y2: cB, class: 'yr' }),
        s('text', { x: i * bw + 4, y: H - 6, class: 'lab' }, w.start.slice(0, 4)));
    });
    const pts = weeks.map((w, i) => `${x(i).toFixed(1)},${y(w.working_sets).toFixed(1)}`);
    svg.append(s('path', { d: `M${x(0)},${mainB} L${pts.join(' L')} L${x(n - 1)},${mainB} Z`, fill: 'url(#tl-g)' }),
      s('path', { d: `M${pts.join(' L')}`, class: 'ln' }));
    weeks.forEach((w, i) => {
      if (recs[i]) svg.append(s('circle', { cx: x(i), cy: top - 8, r: Math.min(3.2, 1.4 + recs[i] * 0.4), class: 'rec' }));
      if (w.distance_m) {
        const hh = Math.max(1, (cB - cTop) * w.distance_m / maxC);
        svg.append(s('rect', { x: i * bw + 0.5, y: cB - hh, width: Math.max(1, bw - 1), height: hh, rx: 1, class: 'cbar' }));
      }
    });
    svg.append(s('text', { x: 2, y: cTop - 4, class: 'lab' }, 'cardio km/wk'));
    const p = hero.period;
    const idx = (iso) => Math.max(0, Math.min(n - 1, weeks.findIndex((w) => addDays(w.start, 6) >= iso)));
    let sel = [idx(p.start), idx(p.end)];
    if (sel[1] < sel[0]) sel = [sel[0], sel[0]];
    const brush = s('rect', { y: 0, height: cB, rx: 3, class: 'brush' });
    const hover = s('line', { x1: -9, x2: -9, y1: 0, y2: cB, class: 'hover' });
    const paint = () => { brush.setAttribute('x', sel[0] * bw); brush.setAttribute('width', (sel[1] - sel[0] + 1) * bw); };
    paint();
    svg.append(brush, hover);
    let from = null;
    const at = (e) => { const r = svg.getBoundingClientRect(); return Math.max(0, Math.min(n - 1, Math.floor((e.clientX - r.left) / r.width * n))); };
    svg.addEventListener('pointerdown', (e) => { from = at(e); svg.setPointerCapture(e.pointerId); sel = [from, from]; paint(); });
    svg.addEventListener('pointermove', (e) => {
      const i = at(e), w = weeks[i];
      hover.setAttribute('x1', x(i)); hover.setAttribute('x2', x(i));
      tooltip(e, `Week of ${dateFull(w.start)}`, [['Gym sessions', num(w.gym_sessions)], ['Working sets', num(w.working_sets)],
        ['Volume', tonnes(w.volume_kg)], ['Cardio', w.distance_m ? km(w.distance_m) : '–'], ['Records', num(recs[i])]]);
      if (from != null) { sel = [Math.min(from, i), Math.max(from, i)]; paint(); }
    });
    svg.addEventListener('pointerup', () => {
      if (from == null) return;
      from = null;
      const start = weeks[sel[0]].start, end = [addDays(weeks[sel[1]].start, 6), once.today].sort()[0];
      go({ range: 'custom', start, end });
    });
    svg.addEventListener('pointerleave', () => { hideTip(); hover.setAttribute('x1', -9); hover.setAttribute('x2', -9); });
    box.replaceChildren(svg, h('div', { class: 'legend' }, h('span', {}, h('i'), 'working sets per week'),
      h('span', {}, h('i', { class: 'c' }), 'cardio distance'), h('span', {}, h('i', { class: 'r' }), 'meaningful records'),
      h('span', { class: 'drag' }, 'Drag to choose a period')));
  };
  requestAnimationFrame(draw);
  once.redraw = draw;
  return h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Your whole history', info('timeline')),
    h('span', {}, `${n} weeks since ${dateFull(weeks[0].start)}`)), box);
}

// ---------------------------------------------------------------- strength

function strength(hero) {
  const st = hero.strength, mi = hero.most_improved;
  const left = h('div', {}, kicker('Strength', 'strength'),
    st.available ? h('div', { class: 'big' }, signed(st.median_pct)) : h('div', { class: 'big na' }, 'n/a'),
    h('p', { class: 'lede' }, st.available ? ['Median change in best estimated 1RM across ', h('b', {}, `${st.n} exercises`), ' done in both periods.']
      : `Not enough data to compare: ${st.reason}.`),
    mi ? h('p', { class: 'lede gap' }, 'Most improved: ', h('b', {}, mi.title), ` ${signed(mi.change_pct)}`) : null,
    h('p', { class: 'lede gap' }, link('/insights#strength-index', { class: 'si-link' }, 'Your Strength Index over the whole history →')));
  const rows = st.exercises || [];
  if (!rows.length) return h('section', { class: 'strength' }, left, h('p', { class: 'lede' }, 'No exercises with an estimated 1RM in both periods.'));
  const shown = rows.length > 10 ? [...rows.slice(0, 7), ...rows.slice(-3)] : rows;
  const max = Math.max(10, ...shown.map((r) => Math.abs(r.change_pct)));
  return h('section', { class: 'strength' }, left, h('div', { class: 'bars' },
    h('div', { class: 'bars-key' }, h('span', {}, 'weaker'), h('span', {}, `${shown.length} of ${rows.length} exercises`), h('span', {}, 'stronger')),
    shown.map((r) => {
      const w = Math.min(50, Math.abs(r.change_pct) / max * 50);
      const fill = css(h('span', { class: `fill ${r.change_pct >= 0 ? 'gain' : 'loss'}` }),
        { left: r.change_pct >= 0 ? '50%' : `${50 - w}%`, width: `${Math.max(w, 0.6)}%` });
      return h('div', { class: 'sbar', tabindex: '0', onmousemove: (e) => tooltip(e, r.title, [['Best e1RM now', `${num1(r.current)} kg`],
        ['Before', `${num1(r.previous)} kg`], ['Sessions', `${r.sessions[0]} now · ${r.sessions[1]} before`]]), onmouseleave: hideTip },
        link(`/exercises/${encodeURIComponent(r.template_id)}`, { class: 't' }, r.title), h('span', { class: 'track' }, fill), h('span', { class: 'p' }, signed(r.change_pct)));
    })));
}

// ---------------------------------------------------------------- muscles (body map + selected muscle)

function muscles(hero, exercises) {
  const days = hero.period.days, perWeek = (x) => x / (days / 7);
  const list = hero.muscles.filter((m) => m.on_map && m.sets > 0);
  if (!selectedMuscle || !list.some((m) => m.muscle === selectedMuscle)) selectedMuscle = list[0]?.muscle || null;
  const max = Math.max(1, ...list.map((m) => m.sets));
  const data = Object.fromEntries(list.map((m) => [m.muscle, { value: m.sets / max }]));
  const pick = (m) => { selectedMuscle = m; paint(); };
  const tipRows = (m) => {
    const x = hero.muscles.find((y) => y.muscle === m);
    return x && x.sets ? [['Sets a week', num1(perWeek(x.sets))], ['Share', `${num1(x.share_pct)}%`], ['Sessions', num(x.workouts)]] : [['', 'Not trained in this period']];
  };
  const mapBox = h('div', { class: 'map' });
  const focus = h('div', { class: 'focus' });
  const ranking = h('div', { class: 'ranking' });
  function paint() {
    mapBox.replaceChildren(bodyMap({ data, ramp: LIME.slice(1), selected: selectedMuscle, onPick: pick, tipRows }),
      h('div', { class: 'ramp-key' }, 'fewer', h('span', { class: 'ramp' }, LIME.slice(1).map((c) => css(h('span'), { background: c }))), 'more sets a week'),
      sexSwitch(paint));
    const m = hero.muscles.find((x) => x.muscle === selectedMuscle);
    const exs = exercises.filter((e) => e.primary_muscle === selectedMuscle).sort((a, b) => b.working_sets - a.working_sets).slice(0, 6);
    focus.replaceChildren(...(m ? [
      kicker('Selected muscle'),
      h('h3', {}, link(`/muscles/${m.muscle}`, {}, MUSCLE[m.muscle], h('span', { class: 'go' }, ' →'))),
      h('p', { class: 'sub' }, `${num1(perWeek(m.sets))} sets a week · ${num1(m.share_pct)}% of your training`),
      h('div', { class: 'kv' },
        h('div', {}, h('b', {}, num1(m.sets), m.change_pct != null ? h('span', { class: 'chip' }, signed(m.change_pct)) : null), h('span', {}, 'credited sets')),
        h('div', {}, h('b', {}, m.previous_sets == null ? '–' : num1(m.previous_sets)), h('span', {}, hero.period.prev_start ? 'the period before' : 'no earlier period')),
        h('div', {}, h('b', {}, num(m.workouts)), h('span', {}, 'sessions')),
        h('div', {}, h('b', {}, tonnes(m.volume_kg)), h('span', {}, 'credited volume'))),
      kicker('Main exercises for it'),
      exs.length ? h('ul', { class: 'exlist' }, exs.map((e) => h('li', {}, e.title, h('span', {}, `${e.working_sets} sets`))))
        : h('p', { class: 'lede' }, 'Only trained as a secondary muscle in this period.'),
    ] : [h('p', { class: 'lede' }, 'No muscle work in this period.')]));
    const prevMax = Math.max(max, ...list.map((x) => x.previous_sets || 0));
    ranking.replaceChildren(...list.slice(0, 12).map((x) => h('button', { type: 'button', class: `rk${x.muscle === selectedMuscle ? ' sel' : ''}`, onclick: () => pick(x.muscle) },
      h('span', {}, MUSCLE[x.muscle]),
      h('span', { class: 'track' }, css(h('span', { class: 'now' }), { width: `${x.sets / prevMax * 100}%` }),
        x.previous_sets ? css(h('span', { class: 'was' }), { left: `${Math.min(100, x.previous_sets / prevMax * 100)}%` }) : null),
      h('em', {}, num1(x.sets)))));
  }
  paint();
  const less = hero.trained_less_than_usual || [];
  return h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Where the work went', info('muscles')), h('span', {}, 'Credited sets: primary 1, secondary ½')),
    list.length ? h('div', { class: 'anatomy' }, mapBox, h('div', { class: 'side' }, focus,
      h('div', { class: 'rank-wrap' }, h('div', { class: 'rank-key' }, h('span', {}, h('i'), 'this period'),
        hero.period.prev_start ? h('span', {}, h('i', { class: 'was' }), 'period before') : null), ranking)))
      : h('p', { class: 'lede' }, 'No gym work in this period.'),
    less.length ? h('p', { class: 'lede less' }, 'Trained less than usual: ',
      less.slice(0, 5).map((x, i) => [i ? ', ' : '', h('b', {}, MUSCLE[x.muscle]), ` (${num1(x.sets_per_week)}/wk, usually ${num1(x.usual_sets_per_week)})`]), info('less')) : null);
}

// ---------------------------------------------------------------- records and the year

function records(hero) {
  const recs = hero.records.slice(0, 10);
  return h('div', {}, h('div', { class: 'head' }, h('h2', {}, 'Records', info('records')), h('span', {}, 'Faded: early or under 1% better')),
    recs.length ? h('ul', { class: 'recs' }, recs.map((r) => h('li', { class: r.meaningful ? '' : 'quiet' },
      h('span', { class: 'd' }, dateShort(r.local_date)),
      h('span', {}, r.template_id ? link(`/exercises/${encodeURIComponent(r.template_id)}`, {}, r.title) : r.title, h('span', { class: 'c' }, RECORD[r.category] || r.category)),
      link(`/sessions/${encodeURIComponent(r.workout_id)}`, { class: 'x' }, r.detail.replace(/ \(was.*\)/, '')))))
      : h('p', { class: 'lede' }, 'No records in this period.'));
}

function calendar(cal) {
  const ramp = LIME.slice(1), byDate = new Map(cal.days.map((d) => [d.local_date, d]));
  const cell = 12, gap = 3;
  const first = new Date(cal.period.start + 'T12:00:00');
  first.setDate(first.getDate() - ((first.getDay() + 6) % 7));
  const last = new Date(cal.period.end + 'T12:00:00');
  const weeks = Math.ceil(((last - first) / 864e5 + 1) / 7);
  const W = weeks * (cell + gap), H = 7 * (cell + gap) + 16;
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'cal', role: 'img', 'aria-label': 'Training calendar, last 12 months' });
  let month = -1;
  for (let w = 0; w < weeks; w++) for (let d = 0; d < 7; d++) {
    const dt = new Date(first); dt.setDate(first.getDate() + w * 7 + d);
    if (dt > last) continue;
    const iso = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
    if (d === 0 && dt.getMonth() !== month) {
      month = dt.getMonth();
      svg.append(s('text', { x: w * (cell + gap), y: H - 3, class: 'cal-m' }, dt.toLocaleDateString('en-GB', { month: 'short' })));
    }
    const day = byDate.get(iso);
    const fill = !day ? 'var(--cal-empty)' : !day.gym_sessions ? ramp[0] : ramp[Math.min(ramp.length - 1, 1 + Math.floor(Math.min(1, day.working_sets / 40) * (ramp.length - 2)))];
    svg.append(s('rect', { x: w * (cell + gap), y: d * (cell + gap), width: cell, height: cell, rx: 2.5, fill, class: day ? 'on' : '',
      onmousemove: (e) => tooltip(e, dateLong(iso), day ? [['Workouts', `${day.gym_sessions} gym · ${day.cardio_sessions} cardio`],
        ['Working sets', num(day.working_sets)], ['Time', hours(day.duration_s)], ['Volume', tonnes(day.volume_kg)],
        ...(day.distance_m ? [['Distance', km(day.distance_m)]] : [])] : [['', 'Rest day']]),
      onmouseleave: hideTip, onclick: () => day && goTo(`/sessions?q=${iso}`) }));
  }
  const gym = cal.days.reduce((a, d) => a + d.gym_sessions, 0);
  return h('div', {}, h('div', { class: 'head' }, h('h2', {}, 'The last 12 months', info('calendar')), h('span', {}, 'Brighter: more working sets')),
    h('div', { class: 'cal-wrap' }, svg),
    h('div', { class: 'facts' }, h('div', {}, h('b', {}, num(gym)), 'gym sessions'), h('div', {}, h('b', {}, num(cal.days.length)), 'active days'),
      h('div', {}, h('b', {}, km(cal.days.reduce((a, d) => a + d.distance_m, 0))), 'cardio')));
}

// ---------------------------------------------------------------- page

async function render() {
  const mine = ++gen;
  if (!once.weeks) {
    const [wk, recs, cal] = await Promise.all([api('series?range=all&bucket=week'), api('records?range=all&meaningful=1&limit=5000'), api('calendar?range=1y')]);
    once.weeks = wk.series; once.today = wk.period.end; once.cal = cal;
    const at = new Map(once.weeks.map((w, i) => [w.start, i]));
    once.recsByWeek = new Array(once.weeks.length).fill(0);
    for (const r of recs.records) {
      const d = new Date(r.local_date + 'T12:00:00'); d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      const i = at.get(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
      if (i != null) once.recsByWeek[i]++;
    }
  }
  const { sum, hero, pause, exercises } = await load(state);
  if (!root || mine !== gen) return;
  if (!sum.last_workout) {
    root.replaceChildren(h('div', { class: 'empty' }, h('h1', {}, 'Nothing here yet.'), h('p', {}, 'Workouts appear after the first sync. Check the System page.')));
    return;
  }
  document.title = 'Training';
  root.replaceChildren(h('div', { class: 'home' },
    statusStrip(sum), once.next ||= nextUp(), tabs(), heading(sum, hero, pause), ...board(hero), timeline(hero), strength(hero), muscles(hero, exercises),
    h('section', { class: 'block duo' }, records(hero), calendar(once.cal))));
}

function go(next) {
  state = next;
  writeState();
  render().catch(fail);
}

function fail(e) {
  if (root) root.replaceChildren(h('div', { class: 'alert' }, `Couldn't load your training data (${e.message}). `,
    h('button', { type: 'button', class: 'link', onclick: () => render().catch(fail) }, 'Try again')));
}

const onResize = () => { clearTimeout(timer); timer = setTimeout(() => once.redraw?.(), 120); };

export function mount(el) {
  root = el;
  cache.clear();                                   // fresh figures each time the page is opened
  for (const k of Object.keys(once)) delete once[k];
  state = readState();
  addEventListener('resize', onResize);
  render().catch(fail);
}

export function unmount() {
  removeEventListener('resize', onResize);
  hideTip();
  root = null;
}
