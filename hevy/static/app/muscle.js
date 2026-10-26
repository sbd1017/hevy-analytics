// One muscle group: how much it's trained, what trains it, what's progressing, history week by week.
import { api, h, s, css, num, num1, tonnes, signed, dateShort, dateFull, MUSCLE, STATUS, chip, link, page, crumb, failed, periodTabs,
  tooltip, hideTip, go } from './lib.js';
import { bodyMap } from './body.js';
import { ensureFigure } from './figure.js';
import { info } from './explain.js';

let root = null, muscleKey = null, range = '3m';
const LIME = ['#2c3a0f', '#41580f', '#5b7a12', '#7a9e14', '#a3cf1c', '#d7ff3a'];

export async function mount(el, muscle) {
  root = el; muscleKey = muscle;
  range = new URLSearchParams(location.search).get('range') || '3m';
  await render();
}

async function render() {
  let d;
  try { await ensureFigure(); d = await api(`muscles/${muscleKey}?range=${range}`); } catch (e) { root?.replaceChildren(failed(e)); return; }
  if (!root) return;
  history.replaceState(null, '', location.pathname + (range === '3m' ? '' : `?range=${range}`));
  const name = MUSCLE[muscleKey] || muscleKey, sm = d.summary, p = d.period;
  const perWeek = sm ? sm.sets / (p.days / 7) : 0;
  const stat = (label, value, sub) => h('div', { class: 'stat' }, h('span', {}, label), h('b', {}, value), sub ? h('small', {}, sub) : null);
  const weekly = weeklyChart(d.weekly, p);
  root.replaceChildren(page(name,
    crumb(link('/insights', {}, 'Insights'), h('span', {}, 'Muscles'), h('span', {}, name)),
    h('div', { class: 'mus-top' },
      h('div', {},
        h('header', { class: 'phead' }, h('h1', {}, name), h('p', {}, `${p.label}. Sets are credited 1 for the main muscle of an exercise and ½ for each secondary one.`, info('muscles'))),
        periodTabs(range, (k) => { range = k; render(); }),
        h('div', { class: 'stats' },
          stat('Credited sets', num1(sm?.sets || 0), sm && sm.change_pct != null ? `${signed(sm.change_pct)} vs the period before` : sm?.previous_sets != null ? `${num1(sm.previous_sets)} the period before` : null),
          stat('Per week', num1(perWeek), `over ${num(p.days)} days`),
          stat('Share of training', sm?.share_pct != null ? `${num1(sm.share_pct)}%` : '–', 'of credited sets on the body map'),
          stat('Sessions', num(sm?.workouts || 0), 'workouts that trained it'),
          stat('Credited volume', tonnes(sm?.volume_kg || 0)),
          stat('Records', num(d.records.meaningful), 'in exercises led by this muscle'))),
      h('div', { class: 'map mini' }, bodyMap({ data: { [muscleKey]: { value: 1 } }, ramp: LIME, selected: muscleKey, tipRows: () => [],
        onPick: (m) => { if (m !== muscleKey) go(`/muscles/${m}`); } }))),
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Every week'), h('span', {}, 'Credited sets per week · shaded: the period above')), weekly),
    h('div', { class: 'two' },
      h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'What trained it'), h('span', {}, p.label)),
        d.contributors.length ? h('div', { class: 'tbl' }, h('div', { class: 'tr th c4' }, h('span', {}, 'Exercise'), h('span', {}, 'Role'), h('span', {}, 'Sets'), h('span', {}, 'Credited')),
          d.contributors.map((c) => link(`/exercises/${encodeURIComponent(c.template_id)}`, { class: 'tr c4' }, h('span', { class: 'ell' }, c.title),
            h('span', {}, c.role === 'primary' ? 'main' : 'secondary'), h('span', {}, num(c.sets)), h('span', {}, num1(c.credited)))))
          : h('p', { class: 'lede' }, 'Not trained in this period.')),
      h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'Progress', info('plateau')), h('span', {}, 'Exercises led by this muscle')),
        d.progress.length ? h('div', { class: 'tbl' }, d.progress.slice(0, 12).map((x) => link(`/exercises/${encodeURIComponent(x.template_id)}`, { class: 'tr c3' },
          h('span', { class: 'ell' }, x.title, x.recent ? null : h('small', {}, ` · last ${dateShort(x.last_date)}`)),
          h('span', {}, x.trend_pct_per_month == null ? '–' : `${signed(x.trend_pct_per_month)}/mo`), h('span', {}, chip(STATUS[x.status])))))
          : h('p', { class: 'lede' }, 'No exercise led by this muscle has enough sessions yet.')))));
}

function weeklyChart(weeks, p) {
  const box = h('div', { class: 'weeks' });
  if (!weeks.length) { box.append(h('p', { class: 'lede' }, 'Never trained.')); return box; }
  requestAnimationFrame(() => {
    const W = Math.max(300, box.clientWidth), H = 170, padB = 20, n = weeks.length, bw = W / n;
    const max = Math.max(4, ...weeks.map((w) => w.sets));
    const y = (v) => (H - padB) * (1 - v / max);
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, role: 'img', 'aria-label': 'Credited sets per week' });
    const i0 = weeks.findIndex((w) => w.start >= p.start.slice(0, 10)) - 1;
    if (i0 >= -1) svg.append(s('rect', { x: Math.max(0, i0) * bw, y: 0, width: W - Math.max(0, i0) * bw, height: H - padB, class: 'band' }));
    svg.append(s('line', { x1: 0, x2: W, y1: y(max / 2), y2: y(max / 2), class: 'grid' }), s('text', { x: W - 2, y: y(max / 2) - 4, 'text-anchor': 'end', class: 'lab' }, `${num1(max / 2)} sets`));
    weeks.forEach((w, i) => {
      if (w.sets) svg.append(s('rect', { x: i * bw + 0.5, y: y(w.sets), width: Math.max(1, bw - 1), height: H - padB - y(w.sets), rx: 1, class: 'wbar',
        onmousemove: (e) => tooltip(e, `Week of ${dateFull(w.start)}`, [['Credited sets', num1(w.sets)]]), onmouseleave: hideTip }));
      if (i && w.start.slice(5) <= '01-07') svg.append(s('text', { x: i * bw + 3, y: H - 4, class: 'lab' }, w.start.slice(0, 4)));
    });
    box.replaceChildren(svg);
  });
  return box;
}

export function unmount() { root = null; }
