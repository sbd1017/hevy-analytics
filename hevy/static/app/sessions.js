// Sessions: every workout, newest first, grouped by month; filter by kind, text or a date.
import { api, h, num, tonnes, hours, km, dateLong, link, page, failed } from './lib.js';
import { fill } from './lib.js';

let root = null;

export async function mount(el) {
  root = el;
  let data;
  try { data = (await api('workouts?range=all')).workouts; } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const q = new URLSearchParams(location.search);
  const st = { kind: q.get('kind') || 'all', text: q.get('q') || '', date: q.get('date') || '' };
  const search = h('input', { type: 'search', class: 'filter-input', placeholder: 'Search titles, or a date like 2025-03', value: st.text || st.date, 'aria-label': 'Search sessions' });
  const kinds = h('div', { class: 'ranges' });
  const list = h('div', { class: 'months' });
  const pick = h('p', { class: 'note' }, 'Tip: open a session, then use Compare to put it side by side with another.');
  function draw() {
    const params = new URLSearchParams();
    if (st.kind !== 'all') params.set('kind', st.kind);
    if (st.text) params.set('q', st.text);
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : ''));
    kinds.replaceChildren(...[['all', 'All'], ['gym', 'Gym'], ['cardio', 'Cardio']].map(([k, l]) => h('button', { type: 'button', class: st.kind === k ? 'on' : '',
      onclick: () => { st.kind = k; draw(); } }, `${l} ${k === 'all' ? data.length : data.filter((w) => w.kind === k).length}`)));
    const t = st.text.toLowerCase();
    const rows = data.filter((w) => (st.kind === 'all' || w.kind === st.kind) && (!t || (w.title || '').toLowerCase().includes(t) || w.local_date.startsWith(t)));
    const months = new Map();
    for (const w of rows) {
      const k = w.local_date.slice(0, 7);
      if (!months.has(k)) months.set(k, []);
      months.get(k).push(w);
    }
    fill(list, ...[...months].slice(0, 36).map(([m, ws]) => h('section', { class: 'month' },
      h('h2', {}, new Date(m + '-15').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }),
        h('span', {}, `${ws.filter((w) => w.kind === 'gym').length} gym · ${ws.filter((w) => w.kind === 'cardio').length} cardio`)),
      ws.map((w) => link(`/sessions/${encodeURIComponent(w.workout_id)}`, { class: `wrow ${w.kind}` },
        h('span', { class: 'wd' }, dateLong(w.local_date)),
        h('span', { class: 'wt' }, w.title || 'Workout', w.meaningful_prs ? h('em', {}, `${w.meaningful_prs} record${w.meaningful_prs > 1 ? 's' : ''}`) : null),
        h('span', {}, hours(w.duration_s)),
        h('span', {}, w.kind === 'gym' ? `${num(w.working_sets)} sets` : km(w.distance_m)),
        h('span', {}, w.kind === 'gym' ? tonnes(w.volume_kg) : ''))))),
      rows.length ? null : h('p', { class: 'lede' }, 'No sessions match.'),
      months.size > 36 ? h('p', { class: 'note' }, 'Showing the 36 most recent months that match; search to narrow it down.') : null);
  }
  search.addEventListener('input', () => { st.text = search.value.trim(); draw(); });
  root.replaceChildren(page('Sessions', h('header', { class: 'phead' }, h('h1', {}, 'Sessions'),
    h('p', {}, `${data.length} workouts. Gym sessions and cardio days are counted separately.`)),
    h('div', { class: 'toolbar' }, search), kinds, pick, list));
  if (st.date) { st.text = st.date; }
  draw();
}

export function unmount() { root = null; }
