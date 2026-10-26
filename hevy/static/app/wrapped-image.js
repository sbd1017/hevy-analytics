// Wrapped as an image: a 1080 × 1350 portrait card drawn on a canvas in the browser (nothing is uploaded anywhere),
// downloaded as a PNG. Uses the same figures as the page.
const W = 1080, H = 1350, M = 84;
const C = { bg: '#0b0d10', panel: '#12151a', line: '#232831', ink: '#eef1f5', muted: '#8b94a3', faint: '#5b6472', accent: '#d7ff3a', gain: '#7a9e14' };
const FONT = '-apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, system-ui, sans-serif';
const nf = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });

function fit(ctx, text, max, weight, size, min = 28) {
  let s = size;
  do { ctx.font = `${weight} ${s}px ${FONT}`; s -= 2; } while (ctx.measureText(text).width > max && s > min);
  return s + 2;
}

function label(ctx, text, x, y) {
  ctx.font = `600 26px ${FONT}`;
  ctx.fillStyle = C.muted;
  ctx.letterSpacing = '4px';
  ctx.fillText(text.toUpperCase(), x, y);
  ctx.letterSpacing = '0px';
}

export function drawWrapped(w, title) {
  const cv = document.createElement('canvas');
  cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, 900);
  g.addColorStop(0, 'rgba(163,207,28,0.22)'); g.addColorStop(1, 'rgba(163,207,28,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  ctx.textBaseline = 'alphabetic';

  // header
  label(ctx, 'Year in Training', M, M + 20);
  ctx.fillStyle = C.ink;
  const ts = fit(ctx, title, W - 2 * M, 760, 140, 64);
  ctx.font = `760 ${ts}px ${FONT}`;
  ctx.fillText(title, M - 6, M + 20 + ts * 0.95);
  let y = M + 40 + ts;

  // the big number
  const t = w.totals;
  ctx.fillStyle = C.accent; ctx.font = `780 190px ${FONT}`;
  ctx.fillText(nf.format(t.workouts), M - 8, y + 175);
  const wW = ctx.measureText(nf.format(t.workouts)).width;
  ctx.fillStyle = C.ink; ctx.font = `600 40px ${FONT}`;
  ctx.fillText('workouts', M + wW + 18, y + 130);
  ctx.fillStyle = C.muted; ctx.font = `400 30px ${FONT}`;
  ctx.fillText(`${nf.format(t.gym_sessions)} gym · ${nf.format(t.cardio_sessions)} cardio`, M + wW + 18, y + 180);
  y += 225;

  // four figures
  const figs = [[nf.format(t.hours), 'hours'], [`${nf.format(t.volume_kg / 1000)} t`, 'lifted'], [nf.format(t.working_sets), 'working sets'],
    [`${nf.format(t.distance_km)} km`, 'cardio']];
  const colW = (W - 2 * M) / 4;
  ctx.strokeStyle = C.line; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(M, y); ctx.lineTo(W - M, y); ctx.stroke();
  figs.forEach(([v, l], i) => {
    const x = M + i * colW;
    ctx.fillStyle = C.ink; ctx.font = `700 ${fit(ctx, v, colW - 20, 700, 64, 30)}px ${FONT}`;
    ctx.fillText(v, x, y + 92);
    ctx.fillStyle = C.muted; ctx.font = `400 28px ${FONT}`;
    ctx.fillText(l, x, y + 134);
  });
  y += 170;
  ctx.beginPath(); ctx.moveTo(M, y); ctx.lineTo(W - M, y); ctx.stroke();
  y += 70;

  // highlights
  const rows = [
    w.strongest_lift && ['Heaviest e1RM', `${nf1.format(w.strongest_lift.e1rm)} kg`, w.strongest_lift.title],
    w.biggest_progression && ['Biggest progression', `${w.biggest_progression.change_pct > 0 ? '+' : ''}${nf1.format(w.biggest_progression.change_pct)}%`, w.biggest_progression.title],
    w.favourite_exercise && ['Favourite exercise', `${nf.format(w.favourite_exercise.sessions)}×`, w.favourite_exercise.title],
    w.longest_streak && ['Longest streak', `${nf.format(w.longest_streak.weeks)} weeks`, `training every week`],
    w.habits && ['Usually', `${w.habits.weekday}s`, `in the ${w.habits.time_of_day}`],
  ].filter(Boolean);
  const rowH = 112, footer = 150;
  const fitRows = Math.max(0, Math.floor((H - footer - y) / rowH));
  for (const [k, v, sub] of rows.slice(0, fitRows)) {
    label(ctx, k, M, y);
    ctx.fillStyle = C.ink; ctx.font = `700 52px ${FONT}`;
    ctx.fillText(v, M, y + 62);
    const vw = ctx.measureText(v).width;
    ctx.fillStyle = C.muted;
    const fs = fit(ctx, sub, W - 2 * M - vw - 24, 400, 34, 22);
    ctx.font = `400 ${fs}px ${FONT}`;
    ctx.fillText(sub, M + vw + 24, y + 60);
    y += rowH;
  }

  // landmark
  const lm = w.landmarks?.volume?.[0];
  if (lm) {
    ctx.fillStyle = C.gain; ctx.fillRect(M, H - M - 92, 6, 64);
    ctx.fillStyle = C.ink; ctx.font = `600 34px ${FONT}`;
    const text = `≈ ${nf1.format(lm.count)} ${lm.thing} lifted`;
    ctx.font = `600 ${fit(ctx, text, W - 2 * M - 30, 600, 34, 22)}px ${FONT}`;
    ctx.fillText(text, M + 26, H - M - 52);
    ctx.fillStyle = C.faint; ctx.font = `400 24px ${FONT}`;
    ctx.fillText(`(${lm.note}; just for fun)`, M + 26, H - M - 18);
  }
  ctx.fillStyle = C.faint; ctx.font = `600 24px ${FONT}`;
  ctx.textAlign = 'right';
  ctx.fillText('Training · from my Hevy log', W - M, H - 40);
  return cv;
}

export function downloadWrapped(w, title) {
  const cv = drawWrapped(w, title);
  cv.toBlob((blob) => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `year-in-training-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}.png`;
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }, 'image/png');
}
