// Router for the single-page app. Same-origin links marked data-link navigate without a reload.
import { BASE, api, h, ago, MUSCLE, dateFull } from './lib.js';
import * as home from './home.js';
import * as system from './system.js';
import * as exercises from './exercises.js';
import * as exercise from './exercise.js';
import * as muscle from './muscle.js';
import * as sessions from './sessions.js';
import * as workout from './workout.js';
import * as compare from './compare.js';
import * as insights from './insights.js';
import * as wrapped from './wrapped.js';
import * as data from './data.js';

const ROUTES = [
  [/^\/$/, home], [/^\/system$/, system], [/^\/exercises$/, exercises], [/^\/exercises\/([^/]+)$/, exercise],
  [/^\/muscles\/([a-z_]+)$/, muscle], [/^\/sessions$/, sessions], [/^\/sessions\/([^/]+)$/, workout],
  [/^\/compare$/, compare], [/^\/insights$/, insights], [/^\/wrapped$/, wrapped], [/^\/data$/, data],
];
const root = document.getElementById('app');
let current = null;

function route() {
  const path = location.pathname.slice(BASE.length) || '/';
  let page = home, params = [];
  for (const [re, mod] of ROUTES) {
    const m = path.match(re);
    if (m) { page = mod; params = m.slice(1).map(decodeURIComponent); break; }
  }
  current?.unmount?.();
  current = page;
  const section = '/' + (path.split('/')[1] || '');
  for (const a of document.querySelectorAll('.bar nav a[data-section]')) {
    a.classList.toggle('here', a.dataset.section === section || (section === '/compare' && a.dataset.section === '/sessions')
      || (section === '/muscles' && a.dataset.section === '/insights'));
  }
  root.replaceChildren(h('p', { class: 'loading' }, 'Loading…'));
  page.mount(root, ...params);
}

export function navigate(href) {
  if (href !== location.href) history.pushState(null, '', href);
  route();
  scrollTo(0, 0);
}

document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-link]');
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  closeSearch();
  navigate(a.href);
});
addEventListener('popstate', route);

// ---------------------------------------------------------------- search: exercises, muscles, workouts, dates
let index = null, palette = null;
async function buildIndex() {
  if (index) return index;
  const [ex, wk] = await Promise.all([api('exercises'), api('workouts?range=all')]);
  index = [
    ...ex.exercises.map((e) => ({ kind: 'Exercise', label: e.title, sub: `${e.lifetime_sessions} sessions`, href: `/exercises/${encodeURIComponent(e.template_id)}`, key: e.title.toLowerCase() })),
    ...Object.entries(MUSCLE).filter(([m]) => !['cardio', 'full_body', 'other'].includes(m)).map(([m, label]) =>
      ({ kind: 'Muscle', label, sub: '', href: `/muscles/${m}`, key: label.toLowerCase() + ' ' + m })),
    ...wk.workouts.map((w) => ({ kind: 'Workout', label: w.title || 'Workout', sub: dateFull(w.local_date), href: `/sessions/${encodeURIComponent(w.workout_id)}`,
      key: `${(w.title || '').toLowerCase()} ${w.local_date} ${dateFull(w.local_date).toLowerCase()}` })),
  ];
  return index;
}
function closeSearch() { palette?.remove(); palette = null; }
async function openSearch() {
  if (palette) return;
  const input = h('input', { type: 'search', placeholder: 'Exercise, muscle, workout or date (e.g. 2025-03 or 12 mar)…', 'aria-label': 'Search', autocomplete: 'off' });
  const list = h('ul', { role: 'listbox' });
  palette = document.body.appendChild(h('div', { class: 'palette', role: 'dialog', 'aria-label': 'Search' },
    h('div', { class: 'palette-box' }, input, list)));
  palette.addEventListener('click', (e) => { if (e.target === palette) closeSearch(); });
  input.focus();
  const all = await buildIndex();
  let sel = 0, results = [];
  const draw = () => {
    const q = input.value.trim().toLowerCase();
    results = q ? all.filter((x) => q.split(/\s+/).every((t) => x.key.includes(t))).slice(0, 30) : all.filter((x) => x.kind !== 'Workout').slice(0, 12);
    sel = Math.min(sel, Math.max(0, results.length - 1));
    list.replaceChildren(...results.map((r, i) => h('li', { role: 'option', 'aria-selected': String(i === sel), class: i === sel ? 'on' : '' },
      h('a', { href: BASE + r.href, 'data-link': true }, h('em', {}, r.kind), r.label, h('span', {}, r.sub)))));
    if (!results.length) list.append(h('li', { class: 'none' }, 'Nothing found.'));
  };
  input.addEventListener('input', () => { sel = 0; draw(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(results.length - 1, sel + 1); draw(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    if (e.key === 'Enter' && results[sel]) { closeSearch(); navigate(BASE + results[sel].href); }
    if (e.key === 'Escape') closeSearch();
  });
  draw();
}
document.getElementById('search-open')?.addEventListener('click', openSearch);
addEventListener('keydown', (e) => {
  if ((e.key === '/' || (e.key === 'k' && (e.metaKey || e.ctrlKey))) && !/INPUT|TEXTAREA/.test(document.activeElement?.tagName)) { e.preventDefault(); openSearch(); }
  if (e.key === 'Escape') closeSearch();
});

// ---------------------------------------------------------------- quiet sync indicator in the header
async function syncBadge() {
  try {
    const s = await api('status');
    const el = document.getElementById('sync');
    const bad = s.sync.state === 'failing' || s.sync.state === 'auth_failed' || !s.sync.worker_alive;
    el.className = `sync${bad ? ' bad' : ''}`;
    el.replaceChildren(h('i'), s.demo ? 'Demo data' : bad ? 'Sync needs attention' : `Synced ${ago(s.sync.last_success_at)}`);
  } catch { /* the page itself shows errors */ }
}
syncBadge();
setInterval(syncBadge, 60000);
route();
