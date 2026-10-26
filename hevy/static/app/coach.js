// Next workout: the homepage "Next up" card and the System page's settings for it. The suggestions themselves
// are made by the worker (hevy/coach.py); this only shows them, asks for a new one, and saves the settings.
import { CSRF, ago, api, dateShort, fill, h, link } from './lib.js';

const kg = (n) => `${+Number(n).toFixed(2)} kg`;
const post = (path, body) => api(path, {
  method: 'POST', body: body ? JSON.stringify(body) : undefined,
  headers: { 'x-csrf-token': CSRF, ...(body ? { 'content-type': 'application/json' } : {}) },
});

function one(s) {
  if (s.reps && s.weight_kg) return `${s.reps} @ ${kg(s.weight_kg)}`;
  if (s.reps) return `${s.reps} reps`;
  if (s.duration_s) return s.duration_s >= 120 ? `${Math.round(s.duration_s / 60)} min` : `${s.duration_s} s`;
  if (s.distance_m) return `${(s.distance_m / 1000).toFixed(1)} km`;
  return 'as you like';
}

// "3 × 5 @ 102.5 kg", "12 @ 27 kg · 10 @ 27 kg", "+2 warm-up"
export function setsText(sets) {
  const work = sets.filter((s) => s.type !== 'warmup');
  const warm = sets.length - work.length;
  const groups = [];
  for (const s of work) {
    const key = one(s) + s.type;
    const last = groups[groups.length - 1];
    if (last && last.key === key) last.n++;
    else groups.push({ key, n: 1, s });
  }
  const parts = groups.map(({ n, s }) => {
    let t = one(s);
    if (n > 1) t = s.reps && s.weight_kg ? `${n} × ${s.reps} @ ${kg(s.weight_kg)}` : `${n} × ${t}`;
    return t + (s.type === 'failure' ? ' (to failure)' : s.type === 'dropset' ? ' (drop set)' : '');
  });
  return { main: parts.join(' · ') || '—', warm };
}

const lastTime = (t) => (t ? `Last: ${t.replace(/\((\d{4}-\d\d-\d\d)\)$/, (_, d) => `(${dateShort(d)})`)}` : 'Not done before');

function pick(sug, first) {
  return h('article', { class: `pick${first ? ' first' : ''}` },
    h('p', { class: 'kicker' }, first ? 'Do this next' : 'Or'),
    h('h3', {}, sug.title),
    sug.why ? h('p', { class: 'why' }, sug.why) : null,
    h('ol', {}, sug.exercises.map((e) => {
      const t = setsText(e.sets);
      return h('li', {},
        link(`/exercises/${encodeURIComponent(e.template_id)}`, { class: 't' }, e.title),
        h('span', { class: 'x' }, t.main),
        h('span', { class: 'c' }, [e.change, t.warm ? `plus ${t.warm} warm-up set${t.warm > 1 ? 's' : ''}` : null, lastTime(e.last_time)]
          .filter(Boolean).join(' · ')));
    })));
}

function source(st) {
  const s = st.suggestion;
  if (!s) return null;
  const by = s.source === 'chatgpt' ? 'Picked by ChatGPT' : s.source === 'openai' ? `Picked by ${s.model}` : 'Picked by the built-in rules';
  const why = s.source === 'rules' && s.openai_error && (st.openai || '').startsWith('on') ? ` (${s.openai_error})` : '';
  return `${by}${why} · ${ago(s.created_at)}`;
}

// ---------------------------------------------------------------- homepage card

// Folded by default: the heading and a one-line peek of the picks; the workouts open on request and
// stay open (or shut) while the page is in use, across the repaints a recalculation causes.
export function nextUp() {
  const el = h('section', { class: 'block nextup', 'aria-live': 'polite' }, h('div', { class: 'head' }, h('h2', {}, 'Next up')), h('p', { class: 'loading' }, 'Loading…'));
  let polls = 0, open = false;

  async function refresh(btn) {
    btn.disabled = true; btn.textContent = 'Asking…';
    try { await post('coach/refresh'); polls = 0; load(); } catch (e) { btn.disabled = false; btn.textContent = 'Try again'; }
  }

  function paint(st) {
    const s = st.suggestion, busy = st.pending;
    const btn = s && s.source === 'chatgpt' && !busy ? null
      : h('button', { type: 'button', class: 'btn-s', disabled: busy, onclick: (e) => refresh(e.currentTarget) }, busy ? 'Working it out…' : 'Recalculate');
    const head = h('div', { class: 'head' }, h('h2', {}, 'Next up'), h('span', { class: 'src' }, source(st), btn));
    if (!s) {
      fill(el, head, h('p', { class: 'sub' }, busy ? 'Working out your first suggestion…'
        : ['No suggestion yet. Choose your programme on the ', link('/system', {}, 'System page'), '.']));
      return;
    }
    const r = s.result;
    const hint = s.source === 'rules' && st.gpt.key_set ? ' Ask your GPT in ChatGPT for a fuller answer: it appears here.' : '';
    const sum = r.summary || hint ? h('p', { class: 'sum' }, (r.summary || '') + hint) : null;
    if (!r.suggestions.length) {
      fill(el, head, sum, h('p', { class: 'sub' }, 'Choose your programme on the ', link('/system', {}, 'System page'), ' to get suggestions.'));
      return;
    }
    const [first, ...rest] = r.suggestions.map((x) => x.title);
    const label = h('span', { class: 'show' }, open ? 'Hide' : 'Show workout');
    const more = h('details', { class: 'nu-more', open },
      h('summary', {}, h('span', { class: 'peek' }, h('b', {}, first), rest.length ? h('span', { class: 'or' }, ` · or ${rest.join(', ')}`) : null), label),
      h('div', { class: 'picks' }, r.suggestions.map((x, i) => pick(x, i === 0))));
    more.addEventListener('toggle', () => { open = more.open; label.textContent = open ? 'Hide' : 'Show workout'; });
    fill(el, head, sum, more);
  }

  async function load() {
    if (!el.isConnected && polls > 0) return;               // the page was left
    try {
      const st = await api('coach');
      paint(st);
      if (st.pending && polls++ < 60) setTimeout(load, 3000);
    } catch (e) {
      fill(el, h('div', { class: 'head' }, h('h2', {}, 'Next up')), h('p', { class: 'sub' }, `Couldn't load the suggestion (${e.message}).`));
    }
  }
  load();
  return el;
}

// ---------------------------------------------------------------- System page settings

export function coachSettings() {
  const el = h('section', { class: 'coach-set' }, h('h2', {}, 'Next workout'), h('div', { class: 'panel' }, h('p', { class: 'loading' }, 'Loading…')));
  const panel = el.lastChild;

  function paint(st, note) {
    const boxes = st.routines.map((r) => h('label', { class: 'check' }, h('input', { type: 'checkbox', value: r.id, checked: r.selected }), h('span', {}, r.title)));
    const text = h('textarea', { rows: 6, maxlength: 4000, spellcheck: 'true', 'aria-label': 'Coaching instructions' });
    text.value = st.instructions;
    const msg = h('p', { class: 'm', role: 'status' }, note || (st.demo ? 'Saving is switched off in the demo.' : ''));
    const save = h('button', { type: 'button', class: 'btn', disabled: st.demo, onclick: async () => {
      save.disabled = true;
      try {
        const ids = boxes.map((b) => b.firstChild).filter((c) => c.checked).map((c) => c.value);
        paint(await post('coach/settings', { programme: ids, instructions: text.value }), 'Saved. A new suggestion is on its way.');
      } catch (e) { save.disabled = false; msg.textContent = `Couldn't save (${e.message}).`; }
    } }, 'Save');
    const reset = h('button', { type: 'button', class: 'btn-s', onclick: async () => {
      try { paint(await post('coach/settings', { instructions: null }), 'Instructions reset to the default.'); } catch (e) { msg.textContent = `Couldn't reset (${e.message}).`; }
    } }, 'Use the default');
    const s = st.suggestion, g = st.gpt;
    const on = (st.openai || '').startsWith('on');
    const copy = (label, text) => h('button', { type: 'button', class: 'btn-s', onclick: async (e) => {
      const b = e.currentTarget;
      try { await navigator.clipboard.writeText(text); b.textContent = 'Copied'; } catch { b.textContent = 'Copy failed: select it by hand'; }
      setTimeout(() => { b.textContent = label; }, 2000);
    } }, label);
    fill(panel,
      h('ul', { class: 'cstat' },
        h('li', {}, h('span', {}, 'Chosen by'), h('b', {}, g.key_set ? 'Your GPT in ChatGPT when you ask it; the built-in rules otherwise'
          : on ? 'OpenAI API, with the built-in rules as fallback' : 'The built-in rules (set up ChatGPT below for more)')),
        on ? h('li', {}, h('span', {}, 'OpenAI API calls in the last 24 h'), h('b', {}, `${st.calls_last_day} of ${st.daily_limit}`)) : null,
        h('li', {}, h('span', {}, 'Latest suggestion'), h('b', {}, s ? `${ago(s.created_at)}, by ${s.source === 'chatgpt' ? 'ChatGPT' : s.source === 'openai' ? s.model : 'the rules'}` : 'none yet')),
        s && s.source === 'rules' && s.openai_error && on ? h('li', {}, h('span', {}, 'Why not OpenAI'), h('b', {}, s.openai_error)) : null),
      h('p', { class: 'kicker gap' }, 'Programme'),
      h('p', { class: 'm' }, st.programme_chosen ? 'Suggestions only ever come from these routines. Edits made in Hevy flow through.'
        : 'Not chosen yet: these are guessed from the folder of your latest routine. Save to confirm.'),
      h('div', { class: 'checks' }, boxes),
      h('p', { class: 'kicker gap' }, 'Instructions'),
      h('p', { class: 'm' }, 'Sent to OpenAI with your last three weeks of training, recent muscle work and last performance of each programme exercise.'),
      text,
      h('div', { class: 'actions' }, save, st.instructions_default ? null : reset, msg),
      h('p', { class: 'kicker gap' }, 'ChatGPT'),
      h('p', { class: 'm' }, st.demo ? 'In your own install, a GPT of your own in ChatGPT can read your data and post its pick here, on your ChatGPT plan (no API billing). Not available in the demo.'
        : g.key_set
        ? `Connected. Key made ${ago(g.key_created_at)}; last used ${g.last_used_at ? ago(g.last_used_at) : 'never'}. Ask your GPT "what's my next workout?" and its answer shows on the homepage.`
        : 'Not set up. Your own GPT in ChatGPT can read this data and post its pick here, on your ChatGPT plan (no API billing).'),
      h('ol', { class: 'steps' },
        h('li', {}, 'On your server, make a key: ', h('code', {}, 'python -m hevy.cli gpt-key new'), ' (see docs/CHATGPT.md for the exact command). It is shown once.'),
        h('li', {}, 'In ChatGPT: Explore GPTs → Create → Configure. Name it, paste the instructions below, and keep it shared with "Only me".'),
        h('li', {}, 'Create new action → Import from URL → paste the schema link. Authentication: API Key, Auth type Bearer, paste the key.'),
        h('li', {}, 'Save, then ask it "what\'s my next workout?" and allow it to talk to your site.')),
      h('div', { class: 'actions' }, copy('Copy GPT instructions', g.instructions), copy('Copy schema link', g.schema_url)),
      h('pre', { class: 'gpt-text' }, g.instructions));
  }

  api('coach').then((st) => paint(st)).catch((e) => fill(panel, h('p', {}, `Couldn't load (${e.message}).`)));
  return el;
}
