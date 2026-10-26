// The muscle map: an anatomical figure, front and back, with one shape per Hevy muscle group.
// Every shape is drawn for one side (x <= 100) and mirrored about the centre line. viewBox per figure: 200 x 380.
import { s, MUSCLE, tooltip, hideTip } from './lib.js';
import { figure as current } from './figure.js';

const MIRROR = 'matrix(-1 0 0 1 200 0)';

// The body outline (one half): head, neck, shoulder, arm and hand, torso, leg and foot, back up the centre line.
const SILHOUETTE = 'M100,4 C88,4 82,13 82,25 C82,35 86,43 92,47 L92,54 C84,58 70,58 60,63 C50,67 45,78 45,92 '
  + 'C44,108 44,124 43,140 C41,156 38,176 36,196 C34,204 33,214 36,222 C38,228 44,228 46,222 C47,214 48,206 48,198 '
  + 'C52,180 56,162 58,146 C60,132 62,118 65,106 L68,106 C72,124 74,140 74,152 C74,164 70,176 70,190 '
  + 'C69,214 70,240 74,268 C74,286 70,300 72,320 C74,336 78,346 80,352 C78,358 72,364 74,370 L96,370 '
  + 'C96,362 95,356 94,350 C94,330 96,310 94,290 C94,282 96,276 96,270 C98,240 98,214 99,200 L100,198 Z';

const DELTOID = 'M66,62 C56,62 48,70 46,84 C45,94 47,104 52,110 C56,100 60,90 66,84 C70,78 72,70 70,64 Z';
const FOREARM = 'M46,146 C50,142 56,142 59,148 C57,164 53,180 49,196 C45,198 41,198 38,196 C39,178 42,160 46,146 Z';

export const FRONT = {
  neck: ['M92,47 C95,49 97,50 99,50 L99,63 L94,63 C93,58 92,52 92,47 Z'],
  traps: ['M92,56 C86,58 76,60 67,62 C76,64 85,64 92,63 Z'],
  shoulders: [DELTOID],
  chest: ['M99,68 L99,100 C90,108 78,108 70,100 C66,94 66,86 68,80 C70,74 74,68 80,66 C88,65 94,66 99,68 Z'],
  biceps: ['M52,112 C56,104 62,100 66,106 C66,118 64,130 60,140 C56,144 50,144 47,140 C46,130 48,120 52,112 Z'],
  forearms: [FOREARM],
  lats: ['M69,107 C75,113 80,122 82,134 C78,134 75,128 73,122 C71,116 70,111 69,107 Z'],
  abdominals: ['M99,110 L99,186 C94,184 90,178 88,170 C86,156 85,132 86,118 C88,112 94,110 99,110 Z',
    'M84,118 C80,128 76,140 76,156 C78,166 82,174 86,180 C85,160 84,138 84,118 Z'],
  abductors: ['M72,170 C76,178 80,186 82,196 C78,206 74,214 71,222 C69,206 69,188 72,170 Z'],
  quadriceps: ['M76,206 C80,198 86,198 90,206 C94,220 96,238 94,256 C92,264 90,270 86,272 C80,272 76,268 74,262 C71,246 71,224 76,206 Z'],
  adductors: ['M90,204 C94,200 98,200 99,202 L99,214 C99,226 97,236 95,246 C93,232 92,216 90,204 Z'],
  calves: ['M76,288 C80,282 88,282 92,290 C94,306 92,322 88,340 C84,342 80,340 78,336 C74,322 73,302 76,288 Z'],
};

export const BACK = {
  neck: ['M92,47 L99,48 L99,54 C96,54 94,53 92,52 Z'],
  traps: ['M99,50 L99,142 C96,134 93,124 91,116 C89,104 87,94 84,88 C80,78 74,70 66,63 C76,60 86,56 92,53 C95,51 97,50 99,50 Z'],
  shoulders: [DELTOID],
  upper_back: ['M68,68 C74,74 80,80 83,88 C86,98 88,108 90,118 C84,121 77,121 71,117 C67,108 65,98 65,88 C65,80 66,74 68,68 Z'],
  triceps: ['M48,108 C52,100 60,98 64,104 C66,116 64,130 60,142 C56,146 50,146 46,142 C45,130 45,118 48,108 Z'],
  forearms: [FOREARM],
  lats: ['M70,121 C77,124 85,124 91,121 C93,131 95,139 98,146 C93,154 87,162 82,170 C78,164 76,152 74,141 C72,133 71,127 70,121 Z'],
  lower_back: ['M99,146 L99,184 C94,184 89,182 85,178 C85,170 87,162 91,156 C94,152 97,149 99,146 Z'],
  abductors: ['M73,177 C78,172 87,172 94,178 C88,181 82,185 77,192 C73,190 71,183 73,177 Z'],
  glutes: ['M78,186 C86,181 94,182 99,187 L99,212 C92,220 82,220 76,214 C70,206 70,195 78,186 Z'],
  hamstrings: ['M76,222 C84,225 92,225 96,222 C97,238 95,256 92,268 C86,272 80,272 77,266 C73,252 72,236 76,222 Z'],
  adductors: ['M97,221 L99,221 L99,236 C99,244 98,250 96,254 C96,240 97,230 97,221 Z'],
  calves: ['M76,286 C80,280 88,280 93,288 C95,302 93,316 88,326 C85,334 83,340 82,346 C80,338 76,330 74,318 C72,306 72,294 76,286 Z'],
};

// Fine anatomy lines drawn over the muscles (not interactive): ab segments, inner quad, hamstring and calf heads.
const DETAIL = {
  front: ['M88,128 L99,128', 'M87,145 L99,145', 'M88,162 L99,162',
    'M90,236 C87,248 85,260 87,270', 'M82,214 C80,232 80,250 82,266'],
  back: ['M86,228 C86,242 86,254 85,266', 'M84,288 C84,300 84,314 85,326', 'M99,60 C92,64 86,70 82,76'],
};

// The feminine figure: the same shapes, warped (torso width by height on the body: narrower neck, shoulders and
// waist, wider hips; arms a little slimmer and closer in), with a breast shape in place of the pecs and smaller deltoids.
const FEMININE = [[0, 1], [40, 0.97], [50, 0.86], [62, 0.88], [104, 0.9], [140, 0.82], [160, 0.95], [188, 1.12], [215, 1.1], [270, 1], [400, 1]];
const FEMININE_DELTOID = 'M68,63 C60,63 54,69 52,80 C51,89 53,97 56,103 C59,95 63,88 67,82 C70,76 72,70 70,65 Z';
const FEMININE_CHEST = 'M96,76 C98,86 98,98 96,108 C93,117 86,121 78,119 C70,117 65,109 65,99 C66,89 71,81 79,76 C85,73 91,73 96,76 Z';

function torso(y) {
  const i = FEMININE.findIndex(([py]) => py >= y);
  if (i <= 0) return FEMININE[Math.max(0, i)][1];
  const [y0, k0] = FEMININE[i - 1], [y1, k1] = FEMININE[i];
  return k0 + (k1 - k0) * (y - y0) / (y1 - y0);
}

// Moves every point of a path for the current figure; `width` (from BMI) scales the whole figure about the centre line.
function shape(d, fig) {
  if (fig.sex !== 'f' && fig.width === 1) return d;
  return d.replace(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g, (_, xs, ys) => {
    let x = +xs;
    const y = +ys;
    if (fig.sex === 'f') x = y > 95 && x < 67 ? 53 + (x - 53) * 0.84 + 6 : 100 - (100 - x) * torso(y);   // arms: slimmer, closer in
    x = 100 - (100 - x) * fig.width;
    return `${Math.round(x * 10) / 10},${y}`;
  });
}

// Render front + back side by side. data: muscle -> { value 0..1 } ; ramp low -> high.
export function bodyMap({ data, ramp, selected, onPick, tipRows, empty = 'var(--body-empty)', frame = 'var(--body-frame)', gapColor = 'var(--bg)' }) {
  const svg = s('svg', { viewBox: '0 0 420 380', class: 'body', role: 'group', 'aria-label': 'Muscle map, front and back' });
  const fig = { sex: current.sex, width: current.width || 1 };
  const half = (d, attrs) => { d = shape(d, fig); return [s('path', { d, ...attrs }), s('path', { d, ...attrs, transform: MIRROR })]; };
  const figure = (shapes, lines, dx, title) => {
    const g = s('g', { transform: `translate(${dx},0)` });
    g.append(...half(SILHOUETTE, { fill: frame, class: 'skin' }));
    for (const [muscle, base] of Object.entries(shapes)) {
      const parts = fig.sex !== 'f' ? base : shapes === FRONT && muscle === 'chest' ? [FEMININE_CHEST]
        : muscle === 'shoulders' ? [FEMININE_DELTOID] : base;
      const d = data[muscle];
      const fill = d && d.value > 0 ? ramp[Math.min(ramp.length - 1, Math.floor(d.value * (ramp.length - 1) + 0.5))] : empty;
      const grp = s('g', {
        class: `m${selected === muscle ? ' sel' : ''}`, tabindex: '0', role: 'button', 'aria-label': `${MUSCLE[muscle]}`,
        onmousemove: (e) => tooltip(e, MUSCLE[muscle], tipRows(muscle)), onmouseleave: hideTip,
        onclick: () => onPick?.(muscle), onkeydown: (e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onPick?.(muscle)),
      });
      for (const p of parts) grp.append(...half(p, { fill, stroke: gapColor, 'stroke-width': 1.4, 'stroke-linejoin': 'round' }));
      g.append(grp);
    }
    const detail = s('g', { class: 'detail', fill: 'none', stroke: gapColor, 'stroke-width': 1, 'stroke-linecap': 'round' });
    for (const d of lines) detail.append(...half(d, {}));
    g.append(detail);
    g.append(s('text', { x: 100, y: 379, 'text-anchor': 'middle', class: 'body-cap' }, title));
    return g;
  };
  svg.append(figure(FRONT, DETAIL.front, 0, 'FRONT'), figure(BACK, DETAIL.back, 220, 'BACK'));
  return svg;
}
