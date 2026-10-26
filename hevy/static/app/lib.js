// Shared front-end helpers. DOM is built with textContent / attributes only: API data is never parsed as HTML.
export const BASE = document.querySelector('meta[name="base"]').content;
export const CSRF = document.querySelector('meta[name="csrf"]').content;

export async function api(path, opts = {}) {
  const r = await fetch(`${BASE}/api/${path}`, { headers: { accept: 'application/json', ...(opts.headers || {}) }, cache: 'no-store', ...opts });
  if (r.status === 401) { location.reload(); throw new Error('signed out'); }
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
  return r.json();
}

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

const NS = 'http://www.w3.org/2000/svg';
export function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}

export const css = (el, props) => { Object.assign(el.style, props); return el; };

// ---------------------------------------------------------------- formatting
const nf0 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });
export const num = (n) => (n == null ? '–' : nf0.format(n));
export const num1 = (n) => (n == null ? '–' : nf1.format(n));
export const tonnes = (kg) => (kg == null ? '–' : kg >= 100000 ? `${nf0.format(kg / 1000)} t` : kg >= 1000 ? `${nf1.format(kg / 1000)} t` : `${nf0.format(kg)} kg`);
export const hours = (sec) => {
  if (!sec) return '0m';
  const hh = Math.floor(sec / 3600), mm = Math.round((sec % 3600) / 60);
  return hh ? `${hh}h ${String(mm).padStart(2, '0')}m` : `${mm}m`;
};
export const km = (m) => (m == null ? '–' : `${nf1.format(m / 1000)} km`);
export const signed = (p) => (p == null ? '–' : `${p > 0 ? '+' : p < 0 ? '−' : '±'}${nf1.format(Math.abs(p))}%`);
const D = (iso) => new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
export const dateLong = (iso) => D(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
export const dateShort = (iso) => D(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
export const dateFull = (iso) => D(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
export function ago(iso, now = Date.now()) {
  if (!iso) return 'never';
  const sec = Math.round((now - Date.parse(iso)) / 1000);
  if (sec < 45) return 'just now';
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} min ago`;
  const hr = Math.round(m / 60);
  return hr < 36 ? `${hr} h ago` : `${Math.round(hr / 24)} days ago`;
}
export const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
export const addDays = (iso, n) => { const d = D(iso); d.setDate(d.getDate() + n); return isoDate(d); };

export const MUSCLE = {
  abdominals: 'Abs', shoulders: 'Shoulders', biceps: 'Biceps', triceps: 'Triceps', forearms: 'Forearms', quadriceps: 'Quads',
  hamstrings: 'Hamstrings', calves: 'Calves', glutes: 'Glutes', abductors: 'Abductors', adductors: 'Adductors', lats: 'Lats',
  upper_back: 'Upper back', traps: 'Traps', lower_back: 'Lower back', chest: 'Chest', neck: 'Neck', cardio: 'Cardio',
  full_body: 'Full body', other: 'Other',
};
export const RECORD = {
  e1rm: 'Estimated 1RM', weight: 'Heaviest weight', reps_at_weight: 'Rep record', set_volume: 'Best set', session_volume: 'Session volume',
  max_reps: 'Most reps', longest_hold: 'Longest hold', longest_distance: 'Longest distance', longest_session: 'Longest session',
  workout_volume: 'Biggest workout',
};

// ---------------------------------------------------------------- tooltip (one per page)
let tip;
export function tooltip(evt, title, rows = []) {
  tip ??= document.body.appendChild(h('div', { class: 'tip', role: 'tooltip' }));
  tip.replaceChildren(h('b', {}, title), ...rows.map(([k, v]) => h('div', {}, h('span', {}, k), h('em', {}, v))));
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, ht = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + ht > innerHeight - 8) y = evt.clientY - ht - pad;
  tip.style.transform = `translate(${Math.max(8, x)}px, ${Math.max(8, y)}px)`;
}
export function hideTip() { if (tip) tip.hidden = true; }

// ---------------------------------------------------------------- shared UI pieces
export const link = (path, attrs = {}, ...kids) => h('a', { href: `${BASE}${path}`, 'data-link': true, ...attrs }, ...kids);

export const STATUS = {
  improving: ['Improving', 'good'], stable: ['Stable', 'calm'], productive_plateau: ['Productive plateau', 'calm'],
  stalled: ['Stalled', 'warn'], declining: ['Declining', 'warn'], insufficient: ['Not enough data', 'none'],
};
export const MOMENTUM = {
  accelerating: ['Accelerating', 'good'], improving: ['Improving', 'good'], maintaining: ['Maintaining', 'calm'],
  plateauing: ['Plateauing', 'warn'], declining: ['Declining', 'warn'], new: ['New', 'calm'],
};
export const chip = ([label, tone]) => h('span', { class: `chip-s ${tone}` }, label);

export const PERIODS = [['28d', '28 days'], ['3m', '3 months'], ['6m', '6 months'], ['1y', 'Year'], ['all', 'All time']];
export function periodTabs(current, onPick, list = PERIODS) {
  return h('div', { class: 'ranges', role: 'tablist', 'aria-label': 'Period' }, list.map(([k, label]) =>
    h('button', { type: 'button', role: 'tab', 'aria-selected': String(k === current), class: k === current ? 'on' : '', onclick: () => onPick(k) }, label)));
}

export function page(title, ...kids) {
  document.title = `Training · ${title}`;
  return h('div', { class: 'page' }, ...kids);
}
export const crumb = (...parts) => h('nav', { class: 'crumb', 'aria-label': 'Breadcrumb' }, parts.map((p, i) => [i ? h('span', {}, '/') : null, p]));
export const loading = () => h('p', { class: 'loading' }, 'Loading…');
export const failed = (e) => h('div', { class: 'alert' }, `Couldn't load this (${e.message}).`);

// Programmatic navigation inside the app (the router listens for popstate).
export function go(path) {
  history.pushState(null, '', `${BASE}${path}`);
  dispatchEvent(new PopStateEvent('popstate'));
  scrollTo(0, 0);
}

// replaceChildren that skips null/false (plain replaceChildren would print "null").
export const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
