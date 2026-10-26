// Exercises: every exercise you've done, searchable, with its progress status.
import { api, h, num, num1, km, dateFull, MUSCLE, STATUS, chip, link, page, failed } from './lib.js';
import { fill } from './lib.js';
import { info } from './explain.js';

let root = null;
const FILTERS = [['all', 'All'], ['improving', 'Improving'], ['stable', 'Stable'], ['productive_plateau', 'Productive plateau'],
  ['stalled', 'Stalled'], ['declining', 'Declining'], ['insufficient', 'Not enough data']];
const SORTS = [['sessions', 'Most done'], ['last', 'Recently done'], ['trend', 'Fastest progress'], ['name', 'A–Z']];

export async function mount(el) {
  root = el;
  let data;
  try { data = (await api('exercises?status=1')).exercises; } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const q = new URLSearchParams(location.search);
  const st = { text: q.get('q') || '', status: q.get('status') || 'all', sort: q.get('sort') || 'sessions', current: q.get('current') === '1' };
  const list = h('div', { class: 'exlist-page' });
  const counts = Object.fromEntries(FILTERS.map(([k]) => [k, k === 'all' ? data.length : data.filter((e) => e.status === k).length]));
  const search = h('input', { type: 'search', class: 'filter-input', placeholder: `Search ${data.length} exercises`, value: st.text, 'aria-label': 'Search exercises' });
  const chips = h('div', { class: 'ranges wrap' });
  const sorter = h('select', { class: 'select', 'aria-label': 'Sort' }, SORTS.map(([k, l]) => h('option', { value: k, selected: k === st.sort }, l)));
  const cur = h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: st.current }), 'Only done in the last 90 days');

  function draw() {
    const params = new URLSearchParams();
    if (st.text) params.set('q', st.text);
    if (st.status !== 'all') params.set('status', st.status);
    if (st.sort !== 'sessions') params.set('sort', st.sort);
    if (st.current) params.set('current', '1');
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : ''));
    chips.replaceChildren(...FILTERS.filter(([k]) => counts[k]).map(([k, l]) => h('button', { type: 'button', class: st.status === k ? 'on' : '',
      onclick: () => { st.status = k; draw(); } }, `${l} ${counts[k]}`)));
    const t = st.text.toLowerCase();
    let rows = data.filter((e) => (!t || e.title.toLowerCase().includes(t) || (MUSCLE[e.primary_muscle] || '').toLowerCase().includes(t))
      && (st.status === 'all' || e.status === st.status) && (!st.current || e.recent));
    rows = rows.sort({
      sessions: (a, b) => b.sessions - a.sessions, last: (a, b) => b.last.localeCompare(a.last), name: (a, b) => a.title.localeCompare(b.title),
      trend: (a, b) => (b.trend_pct_per_month ?? -1e9) - (a.trend_pct_per_month ?? -1e9),
    }[st.sort]);
    fill(list,
      h('div', { class: 'xrow xhead' }, h('span', {}, 'Exercise'), h('span', {}, 'Sessions'), h('span', {}, 'Last done'), h('span', {}, 'Best'),
        h('span', {}, 'Trend / month'), h('span', {}, 'Status')),
      ...rows.map((e) => link(`/exercises/${encodeURIComponent(e.template_id)}`, { class: 'xrow' },
        h('span', { class: 'xt' }, e.title, h('small', {}, MUSCLE[e.primary_muscle] || e.primary_muscle || '')),
        h('span', { 'data-l': 'Sessions' }, num(e.sessions)),
        h('span', { 'data-l': 'Last done' }, dateFull(e.last)),
        h('span', { 'data-l': 'Best' }, e.best_e1rm ? `${num1(e.best_e1rm)} kg e1RM` : e.distance_m ? km(e.distance_m) + ' total' : `${num(e.working_sets)} sets`),
        h('span', { 'data-l': 'Trend' }, e.trend_pct_per_month == null ? '–' : `${e.trend_pct_per_month > 0 ? '+' : ''}${num1(e.trend_pct_per_month)}%`),
        h('span', {}, e.status ? chip(STATUS[e.status]) : chip(['No e1RM', 'none'])))),
      rows.length ? null : h('p', { class: 'lede' }, 'No exercises match.'));
  }
  search.addEventListener('input', () => { st.text = search.value; draw(); });
  sorter.addEventListener('change', () => { st.sort = sorter.value; draw(); });
  cur.firstChild.addEventListener('change', (e) => { st.current = e.target.checked; draw(); });
  root.replaceChildren(page('Exercises',
    h('header', { class: 'phead' }, h('h1', {}, 'Exercises'), h('p', {}, `${data.length} exercises across your whole history. Status is about estimated 1RM progress over each exercise's last 8 sessions.`, info('plateau'))),
    h('div', { class: 'toolbar' }, search, sorter, cur), chips, list));
  draw();
}

export function unmount() { root = null; }
