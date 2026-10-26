// Aligned small multiples on one time axis: one strip per measure, each with its own scale (never two y-axes on one
// chart). Shared crosshair and tooltip across strips; click a point to open it.
import { s, h, tooltip, hideTip, dateFull } from './lib.js';

export function strips(box, { points, layers, onPick, height = 104, markers }) {
  const draw = () => {
    const W = Math.max(280, box.clientWidth), padL = 66, padR = 10, plotW = W - padL - padR;
    if (!points.length) { box.replaceChildren(h('p', { class: 'lede' }, 'No sessions.')); return; }
    const t0 = points[0].t, t1 = points[points.length - 1].t, span = Math.max(1, t1 - t0);
    const x = (t) => padL + (points.length === 1 ? plotW / 2 : (t - t0) / span * plotW);
    const rows = layers.filter((l) => points.some((p) => p[l.key] != null));
    const H = rows.length * height + 26;
    const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, height: H, class: 'strips', role: 'img', 'aria-label': rows.map((l) => l.label).join(', ') + ' per session' });
    rows.forEach((l, ri) => {
      const top = ri * height + 18, bot = top + height - 30;
      const vals = points.map((p) => p[l.key]).filter((v) => v != null);
      let lo = Math.min(...vals), hi = Math.max(...vals);
      if (l.zero) lo = 0;
      if (hi === lo) { hi += 1; lo = Math.max(0, lo - 1); }
      const pad = (hi - lo) * 0.08;
      lo = l.zero ? 0 : lo - pad; hi += pad;
      const y = (v) => (l.invert ? top + (v - lo) / (hi - lo) * (bot - top) : bot - (v - lo) / (hi - lo) * (bot - top));
      svg.append(s('text', { x: 0, y: top - 6, class: 'st-label' }, l.label),
        s('line', { x1: padL, x2: W - padR, y1: bot, y2: bot, class: 'st-base' }),
        s('text', { x: padL - 6, y: y(hi - pad) + 4, 'text-anchor': 'end', class: 'st-tick' }, l.fmt(l.invert ? lo + pad : hi - pad)),
        s('text', { x: padL - 6, y: y(l.zero ? 0 : lo + pad) + 4, 'text-anchor': 'end', class: 'st-tick' }, l.fmt(l.invert ? hi - pad : (l.zero ? 0 : lo + pad))));
      const pts = points.filter((p) => p[l.key] != null);
      if (l.bars) {
        const bw = Math.max(2, Math.min(10, plotW / points.length - 2));
        for (const p of pts) svg.append(s('rect', { x: x(p.t) - bw / 2, y: y(p[l.key]), width: bw, height: Math.max(1, bot - y(p[l.key])), rx: 1.5, fill: l.color }));
      } else {
        if (pts.length > 1) svg.append(s('path', { d: 'M' + pts.map((p) => `${x(p.t).toFixed(1)},${y(p[l.key]).toFixed(1)}`).join(' L'), class: 'st-line', stroke: l.color }));
        for (const p of pts) {
          const mark = markers?.(p, l.key);
          svg.append(s('circle', { cx: x(p.t), cy: y(p[l.key]), r: mark ? 4.5 : pts.length > 60 ? 2 : 3,
            fill: mark ? 'var(--accent)' : l.color, stroke: mark ? 'var(--bg)' : 'none', 'stroke-width': 2 }));
        }
      }
    });
    // x labels: first, last and a few years/months in between
    const fmt = (t) => new Date(t).toLocaleDateString('en-GB', { month: 'short', year: '2-digit' });
    const ticks = Math.min(6, Math.max(2, Math.floor(plotW / 120)));
    for (let i = 0; i < ticks; i++) {
      const t = t0 + span * i / (ticks - 1);
      svg.append(s('text', { x: x(t), y: H - 4, 'text-anchor': i === 0 ? 'start' : i === ticks - 1 ? 'end' : 'middle', class: 'st-tick' }, fmt(t)));
    }
    const cross = s('line', { x1: -9, x2: -9, y1: 8, y2: H - 18, class: 'st-cross' });
    svg.append(cross);
    const nearest = (e) => {
      const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * (W / r.width);
      let best = points[0], bd = Infinity;
      for (const p of points) { const d = Math.abs(x(p.t) - px); if (d < bd) { bd = d; best = p; } }
      return best;
    };
    svg.addEventListener('pointermove', (e) => {
      const p = nearest(e);
      cross.setAttribute('x1', x(p.t)); cross.setAttribute('x2', x(p.t));
      tooltip(e, `${dateFull(p.date)}${p.title ? ` · ${p.title}` : ''}`, [...rows.filter((l) => p[l.key] != null).map((l) => [l.label, l.fmt(p[l.key])]),
        ...(p.note ? [['', p.note]] : []), ...(onPick ? [['', 'Click to open the workout']] : [])]);
    });
    svg.addEventListener('pointerleave', () => { hideTip(); cross.setAttribute('x1', -9); cross.setAttribute('x2', -9); });
    if (onPick) svg.addEventListener('click', (e) => onPick(nearest(e)));
    box.replaceChildren(svg);
  };
  draw();
  return draw;
}
