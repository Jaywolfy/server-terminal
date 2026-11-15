// Liniendiagramme auf <canvas>, ohne Bibliothek. Mit Hover-Anzeige der Werte.

const COLORS = { accent: '#97742f', blue: '#2a3a58', green: '#2f6b4a', teal: '#2b6f78', muted: '#6b7488', grid: 'rgba(24,35,55,0.08)' };
export { COLORS };

export function drawChart(canvas, tip, { series, from, to, format, max = null, timeLabel }) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 50), height = Math.max(rect.height, 50);
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const left = 46, right = 8, top = 6, bottom = 18;
  const plotW = width - left - right, plotH = height - top - bottom;
  const all = series.flatMap((s) => s.points.map((p) => p[1]));
  if (all.length === 0) {
    ctx.fillStyle = COLORS.muted; ctx.font = '12px Instrument Sans, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('Noch keine Daten', width / 2, height / 2);
    canvas.onmousemove = canvas.onmouseleave = null;
    tip.style.display = 'none';
    return;
  }
  const dataMax = Math.max(...all, 0);
  const yMax = max !== null ? max : niceMax(dataMax);
  const x = (t) => left + ((t - from) / Math.max(to - from, 1)) * plotW;
  const y = (v) => top + plotH - (Math.min(v, yMax) / yMax) * plotH;

  ctx.font = '10.5px Instrument Sans, sans-serif'; ctx.fillStyle = COLORS.muted; ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1;
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const value = (yMax / 4) * i, py = Math.round(y(value)) + 0.5;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(width - right, py); ctx.stroke();
    ctx.fillText(format(value, true), left - 6, py);
  }
  ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'center';
  for (let i = 0; i <= 3; i++) {
    const t = from + ((to - from) / 3) * i;
    ctx.textAlign = i === 0 ? 'left' : i === 3 ? 'right' : 'center';
    ctx.fillText(timeLabel(t * 1000), left + (plotW / 3) * i, height - 4);
  }
  for (const s of series) {
    if (s.points.length === 0) continue;
    ctx.beginPath();
    s.points.forEach(([t, v], index) => { const px = x(t), py = y(v); index === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py); });
    ctx.strokeStyle = s.color; ctx.lineWidth = 1.7; ctx.lineJoin = 'round'; ctx.stroke();
    if (s.fill) {
      const last = s.points[s.points.length - 1], first = s.points[0];
      ctx.lineTo(x(last[0]), y(0)); ctx.lineTo(x(first[0]), y(0)); ctx.closePath();
      const gradient = ctx.createLinearGradient(0, top, 0, top + plotH);
      gradient.addColorStop(0, s.color + '44'); gradient.addColorStop(1, s.color + '05');
      ctx.fillStyle = gradient; ctx.fill();
    }
  }

  canvas.onmousemove = (event) => {
    const box = canvas.getBoundingClientRect();
    const mx = event.clientX - box.left;
    const t = from + ((mx - left) / plotW) * (to - from);
    const lines = []; let nearest = null;
    for (const s of series) {
      let best = null;
      for (const p of s.points) if (best === null || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
      if (best && Math.abs(best[0] - t) <= Math.max((to - from) / 40, 6)) { lines.push(`${s.label}: ${format(best[1])}`); nearest = best; }
    }
    if (!nearest) { tip.style.display = 'none'; return; }
    tip.textContent = `${timeLabel(nearest[0] * 1000, true)} · ${lines.join(' · ')}`;
    tip.style.display = 'block';
    const parent = canvas.parentElement.getBoundingClientRect();
    tip.style.left = `${Math.min(Math.max(event.clientX - parent.left + 10, 0), parent.width - tip.offsetWidth)}px`;
    tip.style.top = `${event.clientY - parent.top - 34}px`;
  };
  canvas.onmouseleave = () => { tip.style.display = 'none'; };
}

function niceMax(value) {
  if (value <= 0) return 1;
  const exponent = Math.pow(10, Math.floor(Math.log10(value)));
  for (const step of [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]) if (value <= step * exponent) return step * exponent;
  return 10 * exponent;
}
