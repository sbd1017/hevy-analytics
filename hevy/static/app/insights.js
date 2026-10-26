// Insights: momentum, plateaus, Training DNA (any period vs another), muscle balance, exercise order effect.
import { api, h, s, css, num, num1, signed, dateShort, dateFull, addDays, MUSCLE, STATUS, MOMENTUM, chip, link, page, failed,
  tooltip, hideTip } from './lib.js';
import { info } from './explain.js';

let root = null, dnaBox = null, dnaState = null;
const A_COL = '#7a9e14', B_COL = '#3987e5';       // validated pair on the dark surface

export async function mount(el) {
  root = el;
  let mo, ex, oe, si;
  try { [mo, ex, oe, si] = await Promise.all([api('insights/momentum'), api('exercises?status=1'), api('insights/order'), api('strength-index')]); }
  catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const asOf = mo.as_of;
  const yearBack = (iso) => `${Number(iso.slice(0, 4)) - 1}${iso.slice(4)}`;
  const aStart = addDays(`${asOf.slice(0, 7)}-01`, 0);
  const threeBack = (() => { const d = new Date(asOf + 'T12:00:00'); d.setMonth(d.getMonth() - 3); d.setDate(d.getDate() + 1); return d.toISOString().slice(0, 10); })();
  const PRESETS = [
    ['3m', '3 months vs a year earlier', { start: threeBack, end: asOf }, { start: yearBack(threeBack), end: yearBack(asOf) }],
    ['month', `${new Date(asOf + 'T12:00').toLocaleDateString('en-GB', { month: 'long' })} vs a year earlier`, { start: aStart, end: asOf }, { start: yearBack(aStart), end: yearBack(asOf) }],
    ['year', 'Last 12 months vs the 12 before', { start: yearBack(addDays(asOf, 1)), end: asOf }, { start: yearBack(yearBack(addDays(asOf, 1))), end: yearBack(asOf) }],
  ];
  dnaState = { preset: '3m', a: PRESETS[0][2], b: PRESETS[0][3] };
  dnaBox = h('div', { class: 'dna' });
  const presetBar = h('div', { class: 'ranges wrap' });
  const customA = rangeInputs('A', () => dnaState.a, (v) => { dnaState = { ...dnaState, preset: 'custom', a: v }; drawPresets(); loadDna(); });
  const customB = rangeInputs('B', () => dnaState.b, (v) => { dnaState = { ...dnaState, preset: 'custom', b: v }; drawPresets(); loadDna(); });
  function drawPresets() {
    presetBar.replaceChildren(...PRESETS.map(([k, label, a, b]) => h('button', { type: 'button', class: dnaState.preset === k ? 'on' : '',
      onclick: () => { dnaState = { preset: k, a, b }; drawPresets(); customA.sync(); customB.sync(); loadDna(); } }, label)));
  }
  drawPresets();

  // ---- momentum
  const cols = ['accelerating', 'improving', 'maintaining', 'plateauing', 'declining', 'new'];
  const momentum = h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Momentum', info('momentum')), h('span', {}, `As of your latest workout, ${dateFull(asOf)} · exercises done in the 90 days before it`)),
    h('div', { class: 'mboard' }, cols.map((k) => {
      const list = mo.exercises[k];
      return h('div', { class: `mcol tone-${MOMENTUM[k][1]}` }, h('h3', {}, MOMENTUM[k][0], h('span', {}, num(list.length))),
        list.length ? h('ul', {}, list.slice(0, 8).map((x) => h('li', { onmousemove: (e) => tooltip(e, x.title, x.reasons.slice(0, 3).map((r) => ['', r])), onmouseleave: hideTip },
          link(`/exercises/${encodeURIComponent(x.template_id)}`, {}, x.title), x.trend_pct_per_month != null ? h('em', {}, `${signed(x.trend_pct_per_month)}/mo`) : null)),
          list.length > 8 ? h('li', { class: 'more' }, `+ ${list.length - 8} more`) : null) : h('p', { class: 'none' }, 'None'));
    })),
    mo.muscles.length ? h('div', { class: 'mus-mo' }, h('p', { class: 'kicker' }, 'Muscles · sets a week, last 4 weeks vs the 8 before'),
      h('div', { class: 'mm' }, mo.muscles.map((m) => link(`/muscles/${m.muscle}`, { class: `mmi ${m.status}` },
        h('b', {}, MUSCLE[m.muscle]), h('span', {}, `${num1(m.before_per_week)} → ${num1(m.sets_per_week)}`),
        h('em', {}, m.status === 'rising' ? '▲ rising' : m.status === 'falling' ? '▼ falling' : '● steady'))))) : null);

  // ---- plateaus (current exercises)
  const current = ex.exercises.filter((e) => e.recent && e.status);
  const counts = Object.keys(STATUS).map((k) => [k, current.filter((e) => e.status === k).length]).filter(([, n]) => n);
  const plateaus = h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Where your exercises stand', info('plateau')), h('span', {}, `${num(current.length)} exercises done in the 90 days before your latest workout`)),
    h('div', { class: 'pl' }, counts.map(([k, n]) => link(`/exercises?status=${k}&current=1`, { class: `plc tone-${STATUS[k][1]}` }, h('b', {}, num(n)), h('span', {}, STATUS[k][0])))));

  // ---- order effect
  const maxE = 30;
  const order = h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Does exercise order matter?', info('order')), h('span', {}, 'e1RM when done 4th or later vs 1st–2nd, adjusted for progress')),
    oe.exercises.length ? h('div', { class: 'oelist' }, h('div', { class: 'oe-axis' }, h('span', {}, `−${maxE}%`), h('span', {}, '0'), h('span', {}, `+${maxE}%`)),
      oe.exercises.map((o) => {
        const pos = (v) => `${50 + Math.max(-maxE, Math.min(maxE, v)) / maxE * 50}%`;
        const whisk = css(h('span', { class: 'wh' }), { left: pos(o.interval_90[0]), width: `calc(${pos(o.interval_90[1])} - ${pos(o.interval_90[0])})` });
        const dot = css(h('span', { class: 'dt' }), { left: pos(o.effect_pct) });
        return link(`/exercises/${encodeURIComponent(o.template_id)}`, { class: `oerow ${o.clear ? 'clear' : ''}` },
          h('span', { class: 'ell' }, o.title), h('span', { class: 'oetrack', title: `90% interval ${signed(o.interval_90[0])} to ${signed(o.interval_90[1])}` }, whisk, dot),
          h('b', {}, signed(o.effect_pct)), h('small', {}, `${o.clear ? 'clear' : 'unclear'} · ${o.early_n}/${o.late_n}`));
      }), h('p', { class: 'note' }, 'Dot: observed difference. Line: 90% interval. Bright rows are clear (the interval excludes zero). Patterns in your history, not proof of cause.'))
      : h('p', { class: 'lede' }, 'No exercise has 5+ sessions both early and late in workouts yet.'));

  // ---- DNA
  const dna = h('section', { class: 'block' },
    h('div', { class: 'head' }, h('h2', {}, 'Training DNA', info('dna')), h('span', {}, h('i', { class: 'k a' }), 'period A ', h('i', { class: 'k b' }), 'period B')),
    presetBar, h('div', { class: 'customs' }, customA, customB), dnaBox);

  root.replaceChildren(page('Insights', h('header', { class: 'phead' }, h('h1', {}, 'Insights'),
    h('p', {}, 'Momentum, progress status, how you train and how that has changed. Every judgement shows its reasons and its sample size.')),
    sindex(si), momentum, plateaus, dna, order));
  loadDna();
  if (location.hash) requestAnimationFrame(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'start' }));
}

function sindex(si) {
  const head = h('div', { class: 'head' }, h('h2', {}, 'Strength Index', info('sindex')), h('span', {}, 'Your own composite: 100 = where you started'));
  if (!si.available) return h('section', { class: 'block', id: 'strength-index' }, head, h('p', { class: 'lede' }, `Not available yet: ${si.reason}.`));
  const box = h('div', { class: 'si-chart' });
  requestAnimationFrame(() => {
    const pts = si.series, W = Math.max(300, box.clientWidth), H = 190, pad = 22;
    const lo = Math.min(...pts.map((p) => p.index)), hi = Math.max(...pts.map((p) => p.index));
    const x = (i) => i / Math.max(1, pts.length - 1) * (W - 8) + 4, y = (v) => 8 + (hi - v) / Math.max(1, hi - lo) * (H - pad - 16);
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Strength Index by week' });
    svg.append(s('line', { x1: 0, x2: W, y1: y(100), y2: y(100), class: 'si-base' }), s('text', { x: 4, y: y(100) - 4, class: 'si-lab' }, '100 · start'));
    svg.append(s('path', { d: 'M' + pts.map((p, i) => `${x(i).toFixed(1)},${y(p.index).toFixed(1)}`).join(' L'), class: 'si-line' }));
    pts.forEach((p, i) => { if (i && p.week.slice(5) <= '01-07') svg.append(s('text', { x: x(i), y: H - 4, class: 'si-lab' }, p.week.slice(0, 4))); });
    const hover = s('circle', { r: 4, cx: -9, cy: -9, class: 'si-dot' });
    svg.append(hover);
    svg.addEventListener('pointermove', (e) => {
      const r = svg.getBoundingClientRect(), i = Math.max(0, Math.min(pts.length - 1, Math.round((e.clientX - r.left) / r.width * (pts.length - 1))));
      hover.setAttribute('cx', x(i)); hover.setAttribute('cy', y(pts[i].index));
      tooltip(e, `Week of ${dateFull(pts[i].week)}`, [['Index', num1(pts[i].index)], ['Exercises active', num(pts[i].active)], ['Linked this week', num(pts[i].linked)]]);
    });
    svg.addEventListener('pointerleave', () => { hideTip(); hover.setAttribute('cx', -9); });
    box.replaceChildren(svg);
  });
  return h('section', { class: 'block', id: 'strength-index' }, head,
    h('div', { class: 'si-top' },
      h('div', {}, h('div', { class: 'si-big' }, num1(si.current)), h('p', { class: 'lede' }, `As of the week of ${dateFull(si.as_of_week)}. `,
        si.change_13w_pct != null ? `${signed(si.change_13w_pct)} over 13 weeks, ` : '', si.change_52w_pct != null ? `${signed(si.change_52w_pct)} over a year. ` : '',
        si.peak ? `Peak ${num1(si.peak.index)} (week of ${dateFull(si.peak.week)}).` : '')),
      box),
    h('div', { class: 'tbl si-tbl' }, h('div', { class: 'tr th si-tr' }, ['Component', 'Start level', 'Now', 'Change', 'Status'].map((c) => h('span', {}, c))),
      si.components.map((c) => link(`/exercises/${encodeURIComponent(c.template_id)}`, { class: 'tr si-tr' },
        h('span', { class: 'ell' }, c.title, h('small', {}, ` · ${MUSCLE[c.muscle] || c.muscle} · ${num(c.sessions)} sessions`)),
        h('span', {}, `${num1(c.start_level)} kg`), h('span', {}, `${num1(c.current_level)} kg`), h('span', {}, signed(c.change_pct)),
        h('span', {}, c.active ? 'counting' : `not done since ${dateShort(c.last)}`)))),
    h('p', { class: 'note' }, 'Levels are e1RM (median of the last 3 sessions). The index measures change in your own levels, so it isn\'t comparable with anyone else\'s.'));
}

function rangeInputs(tag, get, set) {
  const from = h('input', { type: 'date', 'aria-label': `Period ${tag} from` }), to = h('input', { type: 'date', 'aria-label': `Period ${tag} to` });
  const wrap = h('div', { class: `cr ${tag.toLowerCase()}` }, h('em', {}, tag), from, h('span', {}, 'to'), to);
  const fire = () => from.value && to.value && from.value <= to.value && set({ start: from.value, end: to.value });
  from.addEventListener('change', fire); to.addEventListener('change', fire);
  wrap.sync = () => { from.value = get().start; to.value = get().end; };
  wrap.sync();
  return wrap;
}

let dnaReq = 0;
async function loadDna() {
  const mine = ++dnaReq, { a, b } = dnaState;
  dnaBox.replaceChildren(h('p', { class: 'loading' }, 'Loading…'));
  const [A, B] = await Promise.all([api(`dna?range=custom&start=${a.start}&end=${a.end}`), api(`dna?range=custom&start=${b.start}&end=${b.end}`)]);
  if (mine !== dnaReq || !root) return;
  const lbl = (x) => `${dateShort(x.start)} ${x.start.slice(0, 4)} – ${dateShort(x.end)} ${x.end.slice(0, 4)}`;
  const pair = (va, vb) => h('span', { class: 'pairbars' },
    css(h('span', { class: 'pb a' }), { width: `${va || 0}%` }), css(h('span', { class: 'pb b' }), { width: `${vb || 0}%` }));
  const diff = (va, vb) => (va == null || vb == null ? '' : `${va - vb > 0 ? '+' : va - vb < 0 ? '−' : '±'}${num1(Math.abs(va - vb))} pts`);
  const trait = (label, va, vb, note) => h('div', { class: 'tr-d' }, h('span', {}, label, note ? h('small', {}, note) : null), pair(va, vb),
    h('em', {}, `${va == null ? '–' : num1(va) + '%'}`, h('i', {}, `${vb == null ? '–' : num1(vb) + '%'}`)), h('b', {}, diff(va, vb)));
  const small = [A, B].filter((x) => !x.enough).length;
  dnaBox.replaceChildren(...[
    h('div', { class: 'dna-heads' }, h('div', { class: 'a' }, h('em', {}, 'A'), lbl(A), h('small', {}, `${num(A.workouts)} workouts · ${num(A.working_sets)} working sets`)),
      h('div', { class: 'b' }, h('em', {}, 'B'), lbl(B), h('small', {}, `${num(B.workouts)} workouts · ${num(B.working_sets)} working sets`))),
    small ? h('p', { class: 'note' }, 'One of the periods has fewer than 30 working sets, so its shares are rough.') : null,
    h('div', { class: 'traits' }, A.traits.map((t, i) => trait(t.label, t.pct, B.traits[i].pct, t.note))),
    h('p', { class: 'kicker gap' }, 'Where credited sets went'),
    h('div', { class: 'traits' }, ['upper', 'lower', 'core'].map((k) => trait({ upper: 'Upper body', lower: 'Lower body', core: 'Core (abs, lower back)' }[k], A.regions[k], B.regions[k]))),
    h('p', { class: 'kicker gap' }, 'Balance', info('balance')),
    h('div', { class: 'bal' }, A.balance.map((x, i) => {
      const y = B.balance[i];
      const split = (v, cls) => h('span', { class: `split ${cls}` }, css(h('span', {}), { width: `${v.a_pct ?? 50}%` }));
      return h('div', { class: 'balrow' }, h('span', {}, x.a), h('div', {}, split(x, 'a'), split(y, 'b')), h('span', {}, x.b),
        h('small', {}, `A ${x.a_pct == null ? '–' : `${num(x.a_pct)} : ${num(x.b_pct)}`} · B ${y.a_pct == null ? '–' : `${num(y.a_pct)} : ${num(y.b_pct)}`}`));
    })),
    h('div', { class: 'facts' },
      ...[['Median gym session', (x) => (x.facts.median_gym_minutes == null ? '–' : `${num(x.facts.median_gym_minutes)} min`)],
        ['Exercises used', (x) => num(x.facts.exercises)], ['New exercises', (x) => num(x.facts.new_exercises)],
        ['Average RPE', (x) => (x.facts.avg_rpe == null ? '–' : `${num1(x.facts.avg_rpe)} (${num(x.facts.rpe_logged_pct)}% logged)`)]]
        .map(([l, f]) => h('div', {}, h('span', {}, l), h('b', {}, f(A)), h('small', {}, `B: ${f(B)}`))))].filter(Boolean));
}

export function unmount() { root = null; hideTip(); }
