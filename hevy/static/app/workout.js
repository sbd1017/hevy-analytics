// One workout: the summary, records, where the work went, every exercise with its sets, and similar sessions.
import { api, h, css, num, num1, tonnes, hours, km, signed, dateLong, dateFull, MUSCLE, RECORD, link, page, crumb, failed } from './lib.js';
import { info } from './explain.js';

let root = null;
const ordinal = (n) => `${n}${['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th'}`;

function setLine(st, measure) {
  if (measure === 'cardio') return [st.distance_m ? km(st.distance_m) : null, st.duration_s ? hours(st.duration_s) : null].filter(Boolean).join(' · ') || '–';
  if (measure === 'time') return `${num(st.duration_s)} s${st.weight_kg ? ` · ${num1(st.weight_kg)} kg` : ''}`;
  if (measure === 'load_distance') return `${num1(st.weight_kg)} kg · ${num(st.distance_m)} m`;
  if (measure === 'load_time') return `${num1(st.weight_kg)} kg · ${num(st.duration_s)} s`;
  if (st.weight_kg) return `${num1(st.weight_kg)} kg × ${num(st.reps)}`;
  return st.reps != null ? `${num(st.reps)} reps` : '–';
}

export async function mount(el, workoutId) {
  root = el;
  let w;
  try { w = await api(`workouts/${encodeURIComponent(workoutId)}`); } catch (e) { root.replaceChildren(failed(e)); return; }
  if (!root) return;
  const avg = w.compared_with_earlier;
  const vs = (v, a) => (a ? h('small', {}, `${signed((v - a) / a * 100)} vs your average`) : null);
  const stat = (label, value, sub) => h('div', { class: 'stat' }, h('span', {}, label), h('b', {}, value), sub);
  const start = new Date(w.start_utc);
  const maxM = Math.max(1, ...w.muscles.map((m) => m.sets));
  const recs = w.records;
  const recByWe = new Map();
  for (const r of recs) if (r.we_id) recByWe.set(r.we_id, [...(recByWe.get(r.we_id) || []), r]);

  root.replaceChildren(page(w.title || 'Workout',
    crumb(link('/sessions', {}, 'Sessions'), h('span', {}, dateFull(w.local_date))),
    h('header', { class: 'phead' }, h('h1', {}, w.title || 'Workout'),
      h('p', {}, `${dateLong(w.local_date)} ${start.getFullYear()} · ${start.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })} · `,
        h('span', { class: `tag ${w.kind === 'gym' ? 'strong' : ''}` }, w.kind === 'gym' ? 'Gym session' : 'Cardio session'), info('sessions')),
      w.description ? h('p', { class: 'desc' }, w.description) : null),
    h('div', { class: 'stats' },
      stat('Duration', hours(w.duration_s), vs(w.duration_s, avg?.avg_duration_s)),
      stat('Working sets', num(w.working_sets), vs(w.working_sets, avg?.avg_working_sets)),
      w.volume_kg ? stat('Volume', tonnes(w.volume_kg), vs(w.volume_kg, avg?.avg_volume_kg)) : null,
      w.distance_m ? stat('Cardio distance', km(w.distance_m), h('small', {}, hours(w.cardio_duration_s))) : null,
      stat('Exercises', num(w.exercises.length), w.warmup_sets ? h('small', {}, `+ ${w.warmup_sets} warm-up sets`) : null),
      stat('Records', num(w.meaningful_prs), w.prs > w.meaningful_prs ? h('small', {}, `+ ${w.prs - w.meaningful_prs} early or small`) : null),
      w.avg_rpe ? stat('Average RPE', num1(w.avg_rpe)) : null),
    avg ? h('p', { class: 'note' }, `Compared with the average of your ${num(avg.workouts)} earlier ${w.kind} sessions.`) : null,
    h('div', { class: 'two' },
      h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'Records', info('records'))),
        recs.length ? h('ul', { class: 'recs' }, recs.map((r) => h('li', { class: r.meaningful ? '' : 'quiet' }, h('span', { class: 'd' }, ''),
          h('span', {}, r.title, h('span', { class: 'c' }, RECORD[r.category] || r.category)), h('span', { class: 'x' }, r.detail.replace(/ \(was.*\)/, '')))))
          : h('p', { class: 'lede' }, 'No records in this workout.')),
      h('section', {}, h('div', { class: 'head' }, h('h2', {}, 'Where the work went', info('muscles')), h('span', {}, 'Credited sets')),
        w.muscles.length ? h('div', { class: 'ranking' }, w.muscles.slice(0, 10).map((m) => link(`/muscles/${m.muscle}`, { class: 'rk' },
          h('span', {}, MUSCLE[m.muscle] || m.muscle), h('span', { class: 'track' }, css(h('span', { class: 'now' }), { width: `${m.sets / maxM * 100}%` })), h('em', {}, num1(m.sets)))))
          : h('p', { class: 'lede' }, 'No muscle work logged.'))),
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Exercises'), h('span', {}, 'Warm-ups are shown faded and never count')),
      w.exercises.map((e) => {
        const rs = recByWe.get(e.id) || [];
        return h('details', { class: 'exc', open: w.exercises.length <= 6 || rs.length > 0 },
          h('summary', {}, h('span', { class: 'pos' }, e.position + 1),
            h('span', { class: 'exn' }, link(`/exercises/${encodeURIComponent(e.template_id)}`, {}, e.title),
              h('small', {}, `${ordinal(e.session_index)} time · ${MUSCLE[e.primary_muscle] || e.primary_muscle || ''}${e.superset_id != null ? ' · superset' : ''}`)),
            h('span', { class: 'exs' }, `${num(e.working_sets)} sets`, e.volume_kg ? ` · ${tonnes(e.volume_kg)}` : '', e.best_e1rm_kg ? ` · e1RM ${num1(e.best_e1rm_kg)} kg` : ''),
            rs.length ? h('em', { class: 'pr' }, `${rs.length} record${rs.length > 1 ? 's' : ''}`) : null),
          e.notes ? h('p', { class: 'desc' }, e.notes) : null,
          h('div', { class: 'sets' }, e.sets.map((st, i) => h('div', { class: `set ${st.working ? '' : 'warm'} ${st.suspect ? 'sus' : ''}` },
            h('span', {}, st.set_type === 'warmup' ? 'W' : st.set_type === 'failure' ? 'F' : st.set_type === 'dropset' ? 'D' : String(e.sets.slice(0, i + 1).filter((x) => x.working).length)),
            h('span', {}, setLine(st, e.measure)),
            h('span', {}, st.rpe != null ? `RPE ${num1(st.rpe)}` : ''),
            h('span', {}, st.e1rm_kg ? `e1RM ${num1(st.e1rm_kg)}` : st.e1rm_note === 'high_reps' ? 'e1RM: too many reps' : ''),
            st.suspect ? h('span', { class: 'sus-n', title: st.suspect }, 'probable typo, left out') : h('span')))));
      })),
    h('section', { class: 'block' }, h('div', { class: 'head' }, h('h2', {}, 'Similar sessions', info('compare')), h('span', {}, 'Same routine, or most of the same exercises')),
      w.similar.length ? h('div', { class: 'tbl' }, w.similar.map((x) => h('div', { class: 'tr c5' },
        link(`/sessions/${encodeURIComponent(x.workout_id)}`, {}, dateFull(x.local_date)), h('span', { class: 'ell' }, x.title || 'Workout'),
        h('span', {}, x.same_routine ? 'same routine' : `${Math.round(x.similarity * 100)}% same exercises`), h('span', {}, x.volume_kg ? tonnes(x.volume_kg) : `${num(x.working_sets)} sets`),
        link(`/compare?a=${encodeURIComponent(x.workout_id)}&b=${encodeURIComponent(w.workout_id)}`, { class: 'btn-s' }, 'Compare'))))
        : h('p', { class: 'lede' }, 'No similar sessions found.'))));
}

export function unmount() { root = null; }
