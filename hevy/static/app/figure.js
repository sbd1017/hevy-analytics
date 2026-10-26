// The body figure's shape: masculine or feminine, and a build from BMI (latest Hevy bodyweight + a height set here).
// Hevy's API has neither sex nor height, so both are settings (System page, or the switch under the homepage map).
import { CSRF, api, h, num1, dateFull } from './lib.js';

export const figure = { sex: 'm', width: 1, demo: false };
let loading = null;

export function ensureFigure() {
  loading ||= api('figure').then((f) => Object.assign(figure, { sex: f.sex, width: f.width, demo: f.demo })).catch(() => figure);
  return loading;
}

async function save(body) {
  return api('figure', { method: 'POST', body: JSON.stringify(body), headers: { 'x-csrf-token': CSRF, 'content-type': 'application/json' } });
}

// Masculine / Feminine switch. In the demo saving is refused, so the choice just applies to this visit.
export function sexSwitch(onChange) {
  const btn = (sex, label) => h('button', { type: 'button', role: 'radio', 'aria-checked': String(figure.sex === sex), class: figure.sex === sex ? 'on' : '',
    onclick: () => {
      if (figure.sex === sex) return;
      figure.sex = sex;
      if (!figure.demo) save({ sex }).catch(() => {});
      onChange();
    } }, label);
  return h('div', { class: 'fig-switch', role: 'radiogroup', 'aria-label': 'Body figure' }, btn('m', 'Masculine'), btn('f', 'Feminine'));
}

// System page: figure and height.
export function figureSettings() {
  const el = h('section', { class: 'coach-set' }, h('h2', {}, 'Body figure'), h('div', { class: 'panel' }, h('p', { class: 'loading' }, 'Loading…')));
  const panel = el.lastChild;
  function paint(f, note) {
    const sex = h('select', { 'aria-label': 'Figure' }, h('option', { value: 'm', selected: f.sex === 'm' }, 'Masculine'),
      h('option', { value: 'f', selected: f.sex === 'f' }, 'Feminine'));
    const height = h('input', { type: 'number', min: 120, max: 230, step: 1, inputmode: 'numeric', placeholder: 'cm', 'aria-label': 'Height in cm', class: 'num-in' });
    if (f.height_cm) height.value = f.height_cm;
    const msg = h('p', { class: 'm', role: 'status' }, note || (f.demo ? 'Saving is switched off in the demo.' : ''));
    const btn = h('button', { type: 'button', class: 'btn', disabled: f.demo, onclick: async () => {
      btn.disabled = true;
      try {
        const out = await save({ sex: sex.value, height_cm: height.value ? Number(height.value) : null });
        Object.assign(figure, { sex: out.sex, width: out.width });
        paint(out, 'Saved.');
      } catch (e) { btn.disabled = false; msg.textContent = `Couldn't save (${e.message}).`; }
    } }, 'Save');
    const build = f.bmi ? `BMI ${num1(f.bmi)} from ${num1(f.weight_kg)} kg (${dateFull(f.weight_date)}) and ${f.height_cm} cm: the figure is drawn ${f.width > 1.005 ? `${Math.round((f.width - 1) * 100)}% broader` : f.width < 0.995 ? `${Math.round((1 - f.width) * 100)}% slimmer` : 'at the standard build'}.`
      : f.weight_kg ? `Add your height to shape the figure from your latest weight (${num1(f.weight_kg)} kg, ${dateFull(f.weight_date)}).`
        : 'Log a bodyweight in Hevy and add your height here to shape the figure to your build.';
    panel.replaceChildren(
      h('p', { class: 'm' }, 'How the muscle map draws your body. Hevy doesn\'t share sex or height, so they\'re set here; weight comes from your latest Hevy body measurement.'),
      h('div', { class: 'fig-form' }, h('label', {}, h('span', {}, 'Figure'), sex), h('label', {}, h('span', {}, 'Height (cm)'), height)),
      h('p', { class: 'm' }, build),
      h('div', { class: 'actions' }, btn, msg));
  }
  api('figure').then((f) => paint(f)).catch((e) => panel.replaceChildren(h('p', {}, `Couldn't load (${e.message}).`)));
  return el;
}
