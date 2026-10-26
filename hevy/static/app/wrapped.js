// Year in Training: a story-style summary for any period, from last month to the last ten years, a calendar year
// or custom dates. Computed live from your data each time, so it is always up to date.
import { api, h, css, num, num1, tonnes, km, signed, dateShort, dateFull, MUSCLE, link, page, failed } from './lib.js';
import { bodyMap } from './body.js';
import { ensureFigure } from './figure.js';
import { info } from './explain.js';
import { downloadWrapped } from './wrapped-image.js';

let root = null, seed = 0, choice = null, years = [];
const LIME = ['#2c3a0f', '#41580f', '#5b7a12', '#7a9e14', '#a3cf1c', '#d7ff3a'];

export async function mount(el) {
  root = el;
  try {
    const months = (await api('series?range=all&bucket=month')).series.filter((m) => m.workouts);
    years = [...new Set(months.map((m) => m.start.slice(0, 4)))].sort().reverse();
  } catch (e) { root.replaceChildren(failed(e)); return; }
  const q = new URLSearchParams(location.search);
  // Default: this year so far; in January and February there's too little of it yet, so last year instead.
  const fallback = new Date().getMonth() < 2 ? 'lastyear' : 'year';
  choice = q.get('year') || (q.get('start') ? 'custom' : q.get('range') || fallback);
  seed = Number(new Date().toISOString().slice(0, 10).replace(/-/g, '')) % 997;
  render(q.get('start'), q.get('end'));
}

function query(start, end) {
  if (choice === 'custom') return `range=custom&start=${start}&end=${end}`;
  if (/^\d{4}$/.test(choice)) return `range=custom&start=${choice}-01-01&end=${choice}-12-31`;
  return `range=${choice}`;
}

async function render(start, end) {
  let w;
  try { await ensureFigure(); w = await api(`wrapped?${query(start, end)}&seed=${seed}`); } catch (e) { root?.replaceChildren(failed(e)); return; }
  if (!root) return;
  history.replaceState(null, '', location.pathname + (/^\d{4}$/.test(choice) ? `?year=${choice}` : choice === 'custom' ? `?start=${start}&end=${end}` : `?range=${choice}`));
  const title = /^\d{4}$/.test(choice) ? choice : choice === 'custom' ? `${dateShort(w.start)} ${w.start.slice(0, 4)} – ${dateFull(w.end)}` : w.period.label;
  const PRESETS = [['year', 'This year so far'], ['lastyear', 'Last year'], ['lastmonth', 'Last month'], ['3m', 'Last 3 months'], ['1y', 'Last 12 months'],
    ['5y', 'Last 5 years'], ['10y', 'Last 10 years'], ['all', 'All time']];
  const pick = (k) => () => { choice = k; render(); };
  const from = h('input', { type: 'date', value: choice === 'custom' ? w.start : '', 'aria-label': 'From' });
  const to = h('input', { type: 'date', value: choice === 'custom' ? w.end : '', 'aria-label': 'To' });
  const go = h('button', { type: 'button', class: 'btn-s', onclick: () => { if (from.value && to.value && from.value <= to.value) { choice = 'custom'; render(from.value, to.value); } } }, 'Show');
  const picker = h('div', { class: 'wpick' },
    h('div', { class: 'ranges wrap' }, PRESETS.map(([k, l]) => h('button', { type: 'button', class: choice === k ? 'on' : '', onclick: pick(k) }, l))),
    h('div', { class: 'ranges wrap' }, h('span', { class: 'wpl' }, 'Years'),
      years.map((y) => h('button', { type: 'button', class: choice === y ? 'on' : '', onclick: pick(y) }, y)),
      h('span', { class: `wcustom${choice === 'custom' ? ' on' : ''}` }, h('span', { class: 'wpl' }, 'Or pick dates'), from, h('span', {}, 'to'), to, go)));
  if (w.empty) {
    root.replaceChildren(page('Wrapped', h('header', { class: 'phead wr-head' }, h('p', { class: 'kicker' }, 'Year in Training', info('wrapped')), h('h1', {}, title)),
      picker, h('p', { class: 'lede' }, 'No workouts in this period.')));
    return;
  }
  const t = w.totals;
  const card = (cls, ...kids) => h('section', { class: `wcard w-${cls}` }, ...kids);
  const k = (text) => h('p', { class: 'wk' }, text);
  const lm = w.landmarks;
  const lmList = (items, unit) => h('ul', { class: 'lm' }, items.map((x) => h('li', {}, h('b', {}, `≈ ${num1(x.count)}`), ` ${x.thing}`, h('small', {}, ` (${x.note})`))));
  const cards = [
    card('hero', k(`${title} · in numbers`), h('div', { class: 'wbig' }, num(t.workouts)), h('p', { class: 'wsub' }, `workouts: ${num(t.gym_sessions)} in the gym, ${num(t.cardio_sessions)} cardio`),
      h('div', { class: 'wrow3' }, h('div', {}, h('b', {}, num(t.hours)), h('span', {}, 'hours')), h('div', {}, h('b', {}, num(t.active_days)), h('span', {}, 'active days')),
        h('div', {}, h('b', {}, num(t.working_sets)), h('span', {}, 'working sets')), h('div', {}, h('b', {}, num(t.reps)), h('span', {}, 'reps'))),
      vsBefore(w), w.coverage.partial ? h('p', { class: 'wsub' }, `Your Hevy history starts on ${dateFull(w.coverage.history_starts)}, so this covers ${spanText(w.span_days)} of it.`) : null),
    card('volume', k('You moved'), h('div', { class: 'wbig' }, tonnes(t.volume_kg)), h('p', { class: 'wsub' }, `${num(t.volume_kg)} kg in total. That's the weight of about`),
      lmList(lm.volume), h('button', { type: 'button', class: 'btn-s', onclick: () => { seed += 1; render(start, end); } }, 'Other comparisons'),
      lm.perspective.some((p) => p.kind === 'lift') ? h('div', { class: 'wpersp' }, h('p', { class: 'wk' }, 'At this pace, lifting'),
        lm.perspective.filter((p) => p.kind === 'lift').map((p) => h('p', {}, h('b', {}, p.thing), ` would take ${p.years}`))) : null),
    t.distance_km ? card('distance', k('Cardio distance'), h('div', { class: 'wbig' }, km(t.distance_km * 1000)), lmList(lm.distance),
      lm.perspective.some((p) => p.kind === 'walk') ? h('p', { class: 'wsub' }, 'At this pace, walking to ', lm.perspective.filter((p) => p.kind === 'walk')
        .map((p, i) => [i ? ' and to ' : '', h('b', {}, p.thing), ` would take ${p.years}`]), '.') : null,
      w.favourite_cardio ? h('p', { class: 'wsub' }, `Mostly ${w.favourite_cardio.title} (${num(w.favourite_cardio.sessions)} sessions).`) : null) : null,
    w.strongest_lift ? card('lift', k('Heaviest estimated 1RM'), h('div', { class: 'wmid' }, `${num1(w.strongest_lift.e1rm)} kg`),
      link(`/exercises/${encodeURIComponent(w.strongest_lift.template_id)}`, { class: 'wlink' }, w.strongest_lift.title),
      h('p', { class: 'wsub' }, `${num1(w.strongest_lift.weight_kg)} kg × ${num(w.strongest_lift.reps)} on ${dateFull(w.strongest_lift.date)}`)) : null,
    w.biggest_record ? card('record', k('Biggest record'), h('div', { class: 'wmid' }, signed(w.biggest_record.improvement_pct)),
      link(`/exercises/${encodeURIComponent(w.biggest_record.template_id)}`, { class: 'wlink' }, w.biggest_record.title),
      h('p', { class: 'wsub' }, `${w.biggest_record.detail} · ${dateFull(w.biggest_record.date)} · ${num(t.records)} meaningful records in all`)) : null,
    w.biggest_progression ? card('prog', k('Biggest progression'), h('div', { class: 'wmid' }, signed(w.biggest_progression.change_pct)),
      link(`/exercises/${encodeURIComponent(w.biggest_progression.template_id)}`, { class: 'wlink' }, w.biggest_progression.title),
      h('p', { class: 'wsub' }, `${num1(w.biggest_progression.from_e1rm)} → ${num1(w.biggest_progression.to_e1rm)} kg e1RM over ${num(w.biggest_progression.sessions)} sessions`)) : null,
    w.favourite_exercise ? card('fav', k('Favourite exercise'), link(`/exercises/${encodeURIComponent(w.favourite_exercise.template_id)}`, { class: 'wmid wlink' }, w.favourite_exercise.title),
      h('p', { class: 'wsub' }, `${num(w.favourite_exercise.sessions)} sessions`)) : null,
    w.most_trained_muscle ? card('muscle', k('Most-trained muscle'), h('div', { class: 'wmus' },
      h('div', {}, link(`/muscles/${w.most_trained_muscle.muscle}`, { class: 'wmid wlink' }, MUSCLE[w.most_trained_muscle.muscle]),
        h('p', { class: 'wsub' }, `${num(w.most_trained_muscle.sets)} credited sets`)),
      bodyMap({ data: { [w.most_trained_muscle.muscle]: { value: 1 } }, ramp: LIME, selected: w.most_trained_muscle.muscle, tipRows: () => [] }))) : null,
    card('habits', k('Your habits'), h('div', { class: 'wmid' }, `${w.habits.weekday}s`),
      h('p', { class: 'wsub' }, `${num(w.habits.weekday_sessions)} of ${num(w.habits.of)} gym sessions. Usually in the ${w.habits.time_of_day} (${num(w.habits.time_of_day_sessions)}).`),
      w.most_active_month ? h('p', { class: 'wsub' }, `Busiest month: ${new Date(w.most_active_month.month + '-15').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' })}, ${num(w.most_active_month.workouts)} workouts.`)
        : h('p', { class: 'wsub' }, `Busiest week: from ${dateFull(w.busiest_week.week)}, ${num(w.busiest_week.workouts)} workouts${w.busiest_week.volume_kg ? `, ${tonnes(w.busiest_week.volume_kg)}` : ''}.`)),
    w.years ? yearsCard(w, card, k) : null,
    card('streak', k('Longest streak'), h('div', { class: 'wmid' }, `${num(w.longest_streak.weeks)} weeks`),
      h('p', { class: 'wsub' }, w.longest_streak.from ? `of training every week, from ${dateFull(w.longest_streak.from)}` : ''),
      w.longest_workout ? h('p', { class: 'wsub' }, 'Longest gym session: ', link(`/sessions/${encodeURIComponent(w.longest_workout.workout_id)}`, {}, `${num(w.longest_workout.minutes)} min on ${dateFull(w.longest_workout.date)}`)) : null,
      w.biggest_workout ? h('p', { class: 'wsub' }, 'Biggest workout: ', link(`/sessions/${encodeURIComponent(w.biggest_workout.workout_id)}`, {}, `${tonnes(w.biggest_workout.volume_kg)} on ${dateFull(w.biggest_workout.date)}`)) : null),
    card('new', k('New exercises'), h('div', { class: 'wmid' }, num(w.new_exercises.count)),
      h('p', { class: 'wsub' }, w.new_exercises.examples.filter(Boolean).join(', ') + (w.new_exercises.count > w.new_exercises.examples.length ? '…' : ''))),
    w.dna.length ? card('dna', k('Training DNA'), h('div', { class: 'wdna' }, w.dna.map((x) => h('div', {}, h('span', {}, x.label),
      h('i', {}, css(h('em'), { width: `${x.pct}%` })), h('b', {}, `${num(x.pct)}%`)))),
      link('/insights', { class: 'wlink small' }, 'Compare with another period →')) : null,
  ];
  root.replaceChildren(page('Wrapped',
    h('header', { class: 'phead wr-head' }, h('p', { class: 'kicker' }, 'Year in Training', info('wrapped')), h('h1', {}, title),
      h('p', {}, `${dateFull(w.start)} to ${dateFull(w.end)}`),
      h('button', { type: 'button', class: 'btn-s', onclick: () => downloadWrapped(w, title) }, 'Download as image')),
    picker, h('div', { class: 'wgrid' }, cards.filter(Boolean))));
}

function spanText(days) {
  const months = Math.round(days / 30.44);
  if (months < 12) return `${months} month${months === 1 ? '' : 's'}`;
  const y = Math.floor(months / 12), m = months % 12;
  return `${y} year${y === 1 ? '' : 's'}${m ? ` ${m} month${m === 1 ? '' : 's'}` : ''}`;
}

function vsBefore(w) {
  const v = w.vs_before;
  if (!v) return null;
  if (!v.available) return h('p', { class: 'wsub' }, `Not enough training in the period before (${dateShort(v.prev_start)} – ${dateFull(v.prev_end)}) to compare.`);
  const chip = (label, x) => (x == null ? null : h('span', { class: `wvs ${x > 0 ? 'up' : x < 0 ? 'down' : ''}` }, `${label} ${signed(x)}`));
  return h('p', { class: 'wsub wvs-row' }, `Compared with ${dateShort(v.prev_start)} – ${dateFull(v.prev_end)}: `,
    chip('workouts', v.workouts), chip('volume', v.volume_kg), chip('time', v.duration_s), chip('cardio', v.distance_m));
}

function yearsCard(w, card, k) {
  const max = Math.max(1, ...w.years.map((y) => y.volume_kg));
  return card('years', k('Year by year'), h('div', { class: 'wyears' }, w.years.map((y) => h('div', { class: y.year === w.best_year ? 'best' : '' },
    h('b', {}, y.year), h('i', {}, css(h('em'), { width: `${y.volume_kg / max * 100}%` })),
    h('span', {}, `${num(y.workouts)} workouts · ${tonnes(y.volume_kg)} · ${num(y.hours)} h${y.distance_km ? ` · ${km(y.distance_km * 1000)}` : ''}`)))),
    h('p', { class: 'wsub' }, `Best year by volume: ${w.best_year}.`));
}

export function unmount() { root = null; }
