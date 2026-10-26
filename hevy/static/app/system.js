// The System page: sync health, stored data, analytics state, data notes, Sync now and the next-workout settings.
import { coachSettings } from './coach.js';
import { figureSettings } from './figure.js';
import { BASE, CSRF, h } from './lib.js';

let app = null;                  // the status part, redrawn on every poll (the settings below it are not)

const nf = new Intl.NumberFormat('en-GB');
const num = (n) => (n == null ? '–' : nf.format(n));

function ago(iso, now = Date.now()) {
  if (!iso) return 'never';
  const s = Math.round((now - Date.parse(iso)) / 1000);
  if (s < 0) return inFuture(iso, now);
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const hr = Math.round(m / 60);
  if (hr < 36) return `${hr} h ago`;
  return `${Math.round(hr / 24)} days ago`;
}
function inFuture(iso, now = Date.now()) {
  const s = Math.round((Date.parse(iso) - now) / 1000);
  if (s <= 30) return 'any moment';
  const m = Math.round(s / 60);
  return m < 60 ? `in ${m} min` : `in ${Math.round(m / 60)} h`;
}
const when = (iso) => (iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '–');
const day = (iso) => (iso ? new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '–');
function bytes(n) {
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB']; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
}

const STATE = {
  ok: ['In sync', ''], running: ['Syncing…', 'live'], never: ['Not synced yet', 'warn'],
  failing: ['Sync failing', 'bad'], auth_failed: ['Hevy refused the key', 'bad'], demo: ['Demo data', ''],
};
const KIND = { incremental: 'Check for changes', backfill: 'First full download', reconcile: 'Full comparison', reference: 'Exercises, routines, body' };
const ERR = {
  network: 'The server could not reach Hevy (network).', server: 'Hevy had a problem (server error).',
  rate_limited: 'Hevy asked us to slow down.', auth: 'Hevy refused the API key. Check Hevy Pro and the key on the server.',
  bad_data: 'Hevy sent something unexpected. Nothing was changed.', internal: 'The sync stopped on an internal error. Nothing was changed.',
};
const ISSUE = {
  missing_template: 'Exercise no longer available from Hevy (kept, with its logged name)',
  template_removed_from_hevy: 'Exercise removed from Hevy (history kept)',
  end_before_start: 'Workout ends before it starts', no_end_time: 'Workout without an end time',
  unparseable: 'Workout without a valid start time (left out)', exercise_index_gaps: 'Exercise order had gaps (renumbered)',
  unknown_set_type: 'Set type not documented by Hevy (kept as-is)', malformed_exercise: 'Malformed exercise entries skipped',
  exercise_without_template: 'Exercise without a template (skipped)',
};

let pollTimer = null;
let waitingFor = null;           // request id after pressing Sync now

async function load() {
  const r = await fetch(`${BASE}/api/status`, { headers: { accept: 'application/json' }, cache: 'no-store' });
  if (r.status === 401) { location.reload(); return null; }
  if (!r.ok) throw new Error(`status ${r.status}`);
  return r.json();
}

async function syncNow(btn) {
  btn.disabled = true; btn.textContent = 'Asking the worker…';
  try {
    const r = await fetch(`${BASE}/api/sync`, { method: 'POST', headers: { 'x-csrf-token': CSRF, accept: 'application/json' } });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    waitingFor = (await r.json()).request_id;
    schedule(1500);
  } catch (e) {
    btn.disabled = false; btn.textContent = 'Sync now';
    alert(`Couldn't request a sync: ${e.message}`);
  }
}

function render(s) {
  const sync = s.sync;
  const busy = sync.state === 'running' || !!sync.pending_request;
  const [label, tone] = STATE[busy && sync.state !== 'running' ? 'running' : sync.state] || ['Unknown', 'warn'];
  const last = s.runs[0];
  const subParts = [];
  if (s.demo) subParts.push('About three and a half years of made-up training, regenerated every night. Nothing connects to Hevy.');
  else subParts.push(h('span', {}, 'Last successful sync ', h('strong', {}, ago(sync.last_success_at))));
  if (!busy && sync.next_incremental_at && sync.worker_alive) subParts.push(` · next check ${inFuture(sync.next_incremental_at)}`);
  if (busy && sync.pending_request) subParts.push(' · waiting for the worker');

  const btn = s.demo ? null : h('button', { class: 'btn', type: 'button', disabled: busy, onclick: (e) => syncNow(e.currentTarget) },
    busy ? 'Syncing…' : 'Sync now');

  const alerts = [];
  if (!sync.worker_alive) alerts.push(h('div', { class: 'alert warn' },
    'The sync worker is not running, so nothing updates automatically and Sync now will wait. ',
    sync.worker_heartbeat_at ? `Last seen ${ago(sync.worker_heartbeat_at)}.` : 'It has never reported in.'));
  if (sync.consecutive_failures && sync.last_error_kind) alerts.push(h('div', { class: 'alert' },
    h('strong', {}, ERR[sync.last_error_kind] || 'The last sync failed.'), ' ',
    `${sync.consecutive_failures} failed attempt${sync.consecutive_failures > 1 ? 's' : ''} in a row; retrying automatically with back-off. `,
    'Your data is unchanged.'));
  if (s.offsite?.stale) alerts.push(h('div', { class: 'alert warn' },
    `The last off-site copy of the database was ${ago(s.offsite.at)}. The daily off-site upload may be failing.`));
  if (s.data_origin && s.data_origin !== 'hevy' && !s.demo) alerts.push(h('div', { class: 'alert warn' },
    `Development database (${s.data_origin}).`));

  const c = s.counts;
  const figs = [
    [num(c.workouts), 'workouts'], [num(c.sets), 'sets logged'], [num(c.exercises_used), 'exercises used'],
    [num(c.body_measurements), 'body measurements'], [num(c.workout_versions), 'workout versions kept'],
    [num(c.deleted_workouts), 'deleted in Hevy (kept)'],
  ];

  const runs = h('ul', { class: 'runs' }, s.runs.length ? s.runs.map((r) => {
    const tone = r.status === 'ok' ? '' : r.status === 'running' ? 'live' : 'bad';
    const bits = [];
    if (r.status === 'failed') bits.push(ERR[r.error_kind] || 'Failed');
    else if (r.status === 'running') bits.push('Running…');
    else {
      if (r.workouts_changed) bits.push(`${r.workouts_changed} new or edited`);
      if (r.workouts_deleted) bits.push(`${r.workouts_deleted} deleted`);
      if (r.other_changed) bits.push(`${r.other_changed} other changes`);
      if (!bits.length) bits.push('No changes');
    }
    bits.push(`${r.api_requests} request${r.api_requests === 1 ? '' : 's'}`);
    return h('li', {}, h('span', { class: `dot ${tone}` }),
      h('div', {}, h('div', { class: 'k' }, KIND[r.kind] || r.kind), h('div', { class: 'm' }, bits.join(' · '))),
      h('span', { class: 't', title: when(r.started_at) }, ago(r.started_at)));
  }) : h('li', {}, h('span'), h('div', { class: 'm' }, 'No syncs yet.'), h('span')));

  const kv = (pairs) => h('dl', { class: 'kv' }, pairs.map(([k, v]) => h('div', {}, h('dt', {}, k), h('dd', {}, v))));
  const details = kv([
    ['History', `${day(s.range.first)} → ${day(s.range.last)}`],
    ['Workouts in Hevy', sync.hevy_workout_count == null ? '–' : num(sync.hevy_workout_count)],
    ['Last full comparison', ago(sync.last_full_sync_at)],
    ['Exercises/routines refreshed', ago(sync.last_reference_at)],
    ['Checks every', `${Math.round(sync.interval_s / 60)} min`],
    ['Analytics', s.analytics.built_at ? `${ago(s.analytics.built_at)} · ${s.analytics.build_ms} ms` : 'not built yet'],
    ['Calculation rules', `v${s.analytics.current_rules}${s.analytics.version && s.analytics.version !== String(s.analytics.current_rules) ? ' (rebuilding)' : ''}`],
    ['Database', bytes(s.db_bytes)],
    ['Off-site copy', s.offsite ? `${ago(s.offsite.at)} · ${s.offsite.copies} kept` : 'not yet'],
    ['Schema', `v${s.schema_version}`],
    ['App', `v${s.version} (${s.env})`],
  ]);

  const notes = [
    ...s.issues.map((i) => h('li', {}, h('div', { class: `sev-${i.severity}` }, ISSUE[i.code] || i.code), h('span', { class: 't' }, num(i.n)))),
    ...s.suspect_sets.map((x) => h('li', {},
      h('div', {}, h('div', { class: 'sev-warning' }, `${x.title}: ${num(x.weight_kg)} kg × ${num(x.reps)}`),
        h('div', { class: 'm' }, `Probable typo, left out of volume and records. ${x.suspect}. Fix it in Hevy and it updates here.`)),
      h('span', { class: 't' }, day(x.local_date)))),
  ];
  if (s.analytics.error) notes.unshift(h('li', {}, h('div', { class: 'sev-error' }, `Analytics rebuild failed (${s.analytics.error}); figures may be stale`), h('span')));
  const issues = notes.length ? h('ul', { class: 'runs issues' }, notes) : h('p', {}, 'None found.');

  app.replaceChildren(
    h('section', { class: 'hero' },
      h('div', {}, h('p', { class: 'eyebrow' }, 'Hevy sync'),
        h('h1', { class: 'state' }, h('span', { class: `dot ${tone}` }), label),
        h('p', { class: 'sub' }, subParts)),
      btn),
    ...alerts,
    h('section', { class: 'figures', 'aria-label': 'Stored data' }, figs.map(([b, t]) => h('div', { class: 'fig' }, h('b', {}, b), h('span', {}, t)))),
    h('div', { class: 'cols' },
      h('section', {}, h('h2', {}, 'Recent syncs'), h('div', { class: 'panel' }, runs)),
      h('div', { class: 'stack' },
        h('section', {}, h('h2', {}, 'Details'), h('div', { class: 'panel' }, details)),
        h('section', {}, h('h2', {}, 'Data notes'), h('div', { class: 'panel' }, issues)))),
  );
  return busy;
}

function schedule(ms) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(tick, ms);
}

async function tick() {
  if (!app) return;
  try {
    const s = await load();
    if (!s || !app) return;                     // signed out, or the page was left while loading
    const busy = render(s);
    if (waitingFor && !busy) waitingFor = null;
    schedule(busy || waitingFor ? 2000 : 30000);
  } catch (e) {
    app.replaceChildren(h('div', { class: 'alert' }, `Couldn't load the status (${e.message}). Retrying…`));
    schedule(10000);
  }
}

const onVisible = () => { if (app && !document.hidden) schedule(0); };

export function mount(root) {
  app = h('div');
  root.replaceChildren(app, coachSettings(), figureSettings());
  document.title = 'Training · System';
  document.addEventListener('visibilitychange', onVisible);
  tick();
}

export function unmount() {
  clearTimeout(pollTimer);
  document.removeEventListener('visibilitychange', onVisible);
  app = null;
}
