// Year skyline: a contribution-graph grid (52 weeks x 7 days) that morphs into an isometric 3D heatmap.
// Plain canvas. Data arrives on window 'alpine:daily' (the same daily rows the report uses).
(() => {
  const canvas = document.getElementById('skyline'); if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const tip = document.getElementById('sky-tip'), stats = document.getElementById('sky-stats');
  const metricSel = document.getElementById('sky-metric'), toggle = document.getElementById('sky-3d');
  const METRICS = {
    water_gal: { label: 'water', unit: 'gal', fmt: v => Math.round(v).toLocaleString() + ' gal' },
    heat_exported_mw: { label: 'heat sold', unit: 'MWh', fmt: v => Math.round(v).toLocaleString() + ' MWh' },
    heat_tower_mw: { label: 'towers', unit: 'MWh', fmt: v => Math.round(v).toLocaleString() + ' MWh' },
    cost_usd: { label: 'net cost', unit: '$', fmt: v => '$' + Math.round(v / 1000) + 'k' },
  };
  // red ramp, dark to hot (contribution-graph style, five steps plus a floor for zero)
  const RAMP = ['#1a1416', '#4a1d1d', '#7f1d1d', '#b91c1c', '#ef4444', '#fca5a5'];
  let rows = [], metric = 'water_gal', k3 = 0, target = 1, hover = -1, t0 = performance.now(), yaw = 0;
  const year = 2023, start = new Date(year, 0, 1), startDow = start.getDay();

  window.addEventListener('alpine:daily', e => { rows = e.detail.rows; computeStats(); });
  metricSel.onchange = () => { metric = metricSel.value; computeStats(); };
  toggle.onclick = () => { target = target ? 0 : 1; toggle.textContent = target ? 'Flat' : '3D'; };
  canvas.addEventListener('mousemove', e => { const r = canvas.getBoundingClientRect(); hover = pick(e.clientX - r.left, e.clientY - r.top); });
  canvas.addEventListener('mouseleave', () => { hover = -1; tip.style.display = 'none'; });
  let drag = null;
  canvas.addEventListener('pointerdown', e => { drag = e.clientX; });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointermove', e => { if (drag != null) { yaw = Math.max(-0.6, Math.min(0.6, yaw + (e.clientX - drag) * 0.004)); drag = e.clientX; } });

  function val(r) { return r ? +r[metric] : 0; }
  function computeStats() {
    if (!rows.length) return;
    const vals = rows.map(val), total = vals.reduce((a, b) => a + b, 0), bi = vals.indexOf(Math.max(...vals));
    const avg = total / vals.length, above = vals.filter(v => v > avg).length, lo = Math.min(...vals);
    const m = METRICS[metric];
    stats.innerHTML = `<span><b>${m.fmt(total)}</b> this year</span><span>busiest day <b>${dayLabel(bi)}</b> at ${m.fmt(vals[bi])}</span><span>quietest ${m.fmt(lo)}</span><span><b>${above}</b> days above the average</span>`;
  }
  function dayLabel(d) { return new Date(year, 0, 1 + d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); }

  // geometry. Flat: a calendar grid. 3D: 2:1 isometric; (w, d) rotated by yaw around the grid centre, heights up.
  let cellW = 14, gap = 3, originX = 40, originY = 24, maxV = 1, minV = 0;
  function layout() { const W = canvas.clientWidth; cellW = Math.max(8, Math.floor((W - 80) / 53) - gap); }
  const pitch = cellW => cellW + gap;
  function iso(w, d) {
    const s = pitch(cellW), cw = 26, cd = 3;
    const rw = (w - cw) * Math.cos(yaw) - (d - cd) * Math.sin(yaw), rd = (w - cw) * Math.sin(yaw) + (d - cd) * Math.cos(yaw);
    const cx = canvas.clientWidth / 2, cy = canvas.clientHeight * 0.66;
    return { x: cx + (rw - rd) * s * 0.74, y: cy + (rw + rd) * s * 0.37 };
  }
  function project(w, d, h) {
    const fx = originX + w * pitch(cellW), fy = originY + d * pitch(cellW);
    const p = iso(w, d);
    return { x: fx + (p.x - fx) * k3, y: fy + (p.y - h - fy) * k3 };
  }
  // the four corners of a cell's top face at height h, blended flat -> iso
  function topFace(w, d, h) {
    const s = pitch(cellW), fx = originX + w * s, fy = originY + d * s, a = cellW / s;
    const f = [[fx, fy], [fx + cellW, fy], [fx + cellW, fy + cellW], [fx, fy + cellW]];
    const i = [iso(w, d), iso(w + a, d), iso(w + a, d + a), iso(w, d + a)].map(q => [q.x, q.y - h]);
    return f.map((q, k) => [q[0] + (i[k][0] - q[0]) * k3, q[1] + (i[k][1] - q[1]) * k3]);
  }
  function poly(pts) { ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (let k = 1; k < pts.length; k++) ctx.lineTo(pts[k][0], pts[k][1]); ctx.closePath(); ctx.fill(); }
  function pick(mx, my) {
    let best = -1, bd = 1e9;
    for (let i = 0; i < rows.length; i++) {
      const w = Math.floor((i + startDow) / 7), d = (i + startDow) % 7, t = topFace(w, d, 0), px = (t[0][0] + t[2][0]) / 2, py = (t[0][1] + t[2][1]) / 2;
      const dx = mx - px, dy = my - py, dist = dx * dx + dy * dy;
      if (dist < bd && dist < cellW * cellW * 1.5) { bd = dist; best = i; }
    }
    return best;
  }
  function shade(hex, f) { const n = parseInt(hex.slice(1), 16); const r = (n >> 16) * f, g = ((n >> 8) & 255) * f, b = (n & 255) * f; return `rgb(${r | 0},${g | 0},${b | 0})`; }

  function frame() {
    const now = performance.now(), dt = Math.min(0.05, (now - t0) / 1000); t0 = now;
    k3 += (target - k3) * Math.min(1, dt * 4);
    layout();
    const dpr = Math.min(devicePixelRatio, 2), W = canvas.clientWidth, H = canvas.clientHeight;
    if (canvas.width !== W * dpr) { canvas.width = W * dpr; canvas.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    if (!rows.length) { ctx.fillStyle = '#71717a'; ctx.font = '12px "Faculty Glyphic", ui-monospace, monospace'; ctx.fillText('waiting for the year…', 40, 60); requestAnimationFrame(frame); return; }
    const vals = rows.map(val); maxV = Math.max(1e-9, ...vals); minV = Math.min(...vals);
    const norm = v => v <= 0 ? 0 : (maxV - minV < 1e-9 ? 1 : 0.12 + 0.88 * (v - minV) / (maxV - minV));
    const maxH = 95 * k3;
    ctx.fillStyle = '#71717a'; ctx.font = '11px "Faculty Glyphic", ui-monospace, monospace';
    for (let m = 0; m < 12; m++) { const d0 = Math.round((Date.UTC(year, m, 1) - Date.UTC(year, 0, 1)) / 864e5), w = Math.floor((d0 + startDow) / 7); const t = topFace(w, 0, 0); ctx.fillText(['J','F','M','A','M','J','J','A','S','O','N','D'][m], t[0][0], t[0][1] - 8 - 16 * k3); }
    // back-to-front: in this iso, larger (w + d) is nearer the viewer
    const order = rows.map((r, i) => i).sort((a, b) => { const wa = Math.floor((a + startDow) / 7), wb = Math.floor((b + startDow) / 7), da = (a + startDow) % 7, db = (b + startDow) % 7; return (wa + da) - (wb + db); });
    for (const i of order) {
      const r = rows[i], w = Math.floor((i + startDow) / 7), d = (i + startDow) % 7, v = norm(vals[i]);
      const level = v <= 0 ? 0 : 1 + Math.min(4, Math.floor(v * 4.999)), col = RAMP[level];
      const h = v * maxH, top = topFace(w, d, h), base = topFace(w, d, 0);
      if (k3 > 0.02 && h > 0.5) {
        ctx.fillStyle = shade(col, 0.5); poly([top[1], top[2], base[2], base[1]]);   // right wall
        ctx.fillStyle = shade(col, 0.7); poly([top[3], top[2], base[2], base[3]]);   // front wall
      }
      ctx.fillStyle = i === hover ? '#ffffff' : (r.scenario !== 'normal' && level === 0 ? '#3b2a12' : col);
      poly(top);
      if (r.scenario && r.scenario !== 'normal') { ctx.strokeStyle = '#f2a93b'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(top[0][0], top[0][1]); for (let k = 1; k < 4; k++) ctx.lineTo(top[k][0], top[k][1]); ctx.closePath(); ctx.stroke(); }
    }
    if (hover >= 0) {
      const r = rows[hover], m = METRICS[metric], w = Math.floor((hover + startDow) / 7), d = (hover + startDow) % 7, v = val(r), nv = v <= 0 ? 0 : (maxV - minV < 1e-9 ? 1 : 0.12 + 0.88 * (v - minV) / (maxV - minV)), t = topFace(w, d, nv * 95 * k3);
      tip.style.display = 'block'; tip.style.left = (t[1][0] + 10) + 'px'; tip.style.top = (t[1][1] - 6) + 'px';
      tip.innerHTML = `<b>${dayLabel(hover)}</b> · ${m.label} <b>${m.fmt(val(r))}</b>${r.scenario !== 'normal' ? ` · <span style="color:#f2a93b">${r.scenario.replace('_', ' ')}</span>` : ''}<br><span style="color:#a1a1aa">to network ${(r.heat_exported_mw / 24).toFixed(0)} MW · towers ${(r.heat_tower_mw / 24).toFixed(0)} MW · pit ${Math.round(r.storage_soc * 100)}%</span>`;
    }
    requestAnimationFrame(frame);
  }
  frame();
})();
