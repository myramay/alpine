// Alpine site scene, light theme: the real Lansing, NY site in 3D as plain matte blocks on a light ground.
// 1,363 OpenStreetMap footprints (merged into one mesh), the Cayuga Lake shoreline, 88 candidate heat customers,
// and the data center at the former coal plant. Alpine's daily rows drive what fills, steams and flows.
// Same DOM contract as site.js: #scene, #site-status, #play, #speed, #day, #day-label, #scen-strip, [data-cam];
// listens for 'alpine:daily', dispatches 'alpine:day'. Scene units: 1 = 10 m. Heights exaggerated.
import * as THREE from 'three';

const canvas = document.getElementById('scene');
if (canvas) init();

async function init() {
  const S = 0.1, HX = 3.0;
  const C = {
    bg: 0xf6f7f6, ground: 0xeef1ef, lake: 0xcfdde9, bld: 0xdfe3e1,
    sell: 0x377a53, sellFill: 0xcee7d7, store: 0x426c9c, storeFill: 0xd7e5f5,
    tower: 0xb33f3f, towerFill: 0xf0d6d6, dry: 0x8a918d, dryFill: 0xd9dddb,
    water: 0x3f8fa8, dc: 0xfbfbfa, dcEdge: 0xc98a2b, accent: 0xc98a2b, cand: 0xd3d8d5, rim: 0xd5dad7,
  };
  const wrap = canvas.parentElement;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.NoToneMapping;   // keep the palette exact on a light page
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(C.bg);
  scene.fog = new THREE.Fog(C.bg, 700, 1500);
  const camera = new THREE.PerspectiveCamera(40, 2, 0.5, 2000);
  scene.add(new THREE.HemisphereLight(0xffffff, 0xc3c8c5, 1.7));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3); sun.position.set(260, 520, 180); scene.add(sun);
  const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.95, metalness: 0, ...extra });
  const pickables = [];
  const st = document.getElementById('site-status');
  st.textContent = 'Loading the town…';

  let geo;
  try { geo = await (await fetch('geo/lansing.json')).json(); } catch (e) { st.textContent = 'Could not load geo/lansing.json: ' + e.message; return; }
  // world: x east, z south (y_m north -> -z)
  const W = (x, y) => [x * S, -y * S];

  // ground + lake (flat, matte)
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(4000, 4000), new THREE.MeshBasicMaterial({ color: C.ground })).rotateX(-Math.PI / 2));
  const lakeMat = new THREE.MeshBasicMaterial({ color: C.lake, side: THREE.DoubleSide });
  for (const poly of geo.water) {
    if (poly.length < 3) continue;
    const sh = new THREE.Shape(poly.map(([x, y]) => new THREE.Vector2(x * S, y * S)));
    const m = new THREE.Mesh(new THREE.ShapeGeometry(sh), lakeMat);
    m.rotation.x = -Math.PI / 2; m.position.y = 0.05; scene.add(m);
  }

  // real buildings: plain extruded blocks, merged into one geometry / one draw call
  const posParts = [], nrmParts = []; let total = 0, n = 0;
  for (const b of geo.buildings) {
    if (b.p.length < 3) continue;
    const sh = new THREE.Shape(b.p.map(([x, y]) => new THREE.Vector2(x * S, y * S)));
    let g = new THREE.ExtrudeGeometry(sh, { depth: Math.max(0.3, b.h * S * HX), bevelEnabled: false, curveSegments: 1 });
    if (g.index) g = g.toNonIndexed();
    const p = g.attributes.position.array; posParts.push(p); total += p.length;
    g.computeVertexNormals(); nrmParts.push(g.attributes.normal.array);
    g.dispose(); n++;
  }
  if (total) {
    const pos = new Float32Array(total), nrm = new Float32Array(total); let o = 0;
    posParts.forEach((p, i) => { pos.set(p, o); nrm.set(nrmParts[i], o); o += p.length; });
    const bg = new THREE.BufferGeometry();
    bg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); bg.setAttribute('normal', new THREE.BufferAttribute(nrm, 3));
    bg.rotateX(-Math.PI / 2);
    scene.add(new THREE.Mesh(bg, mat(C.bld, { flatShading: true })));
  }

  // candidate heat customers: blocks at their positions, pickable, sized by annual heat; fill toward green when on
  const cands = geo.candidates.slice().sort((a, b) => b.mwh - a.mwh);
  const pipes = [];
  const totalMwh = cands.reduce((a, c) => a + c.mwh, 0);
  const pipeMat = () => new THREE.MeshBasicMaterial({ color: C.sell, transparent: true, opacity: 0, depthWrite: false });
  for (const c of cands) {
    const [x, z] = W(c.x, c.y), h = Math.max(0.8, (c.h || 8) * S * HX), w = Math.max(1.2, Math.sqrt(Math.max(200, c.mwh * 4)) * S);
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, w), mat(C.cand));
    m.position.set(x, h / 2, z); m.userData = { kind: 'cand', c, share: c.mwh / totalMwh, glow: 0 }; scene.add(m); pickables.push(m);
    // schematic route: along the shore road (north-south) first, then inland. Across the lake = not reachable.
    const reachable = c.x > -50; m.userData.reachable = reachable;
    const gate = new THREE.Vector3(9, 0.2, 0), mid = new THREE.Vector3(9, 0.2, z), end = new THREE.Vector3(x, 0.2, z);
    const pts = reachable ? [gate, mid, end] : [], segs = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1], len = a.distanceTo(b); if (len < 0.1) continue;
      const p = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, len, 6), pipeMat());
      p.position.copy(a).lerp(b, 0.5); p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize()); p.visible = false; scene.add(p); segs.push(p);
    }
    const pulse = new THREE.Mesh(new THREE.SphereGeometry(0.3, 10, 8), new THREE.MeshBasicMaterial({ color: C.sell, transparent: true, opacity: 0.85 })); pulse.visible = false; scene.add(pulse);
    pipes.push({ segs, pts, pulse, t: Math.random(), m, reachable });
  }

  // data center (the plant footprint: ~160 x 90 m), dry coolers, towers, pit
  const dc = new THREE.Mesh(new THREE.BoxGeometry(16, 3.2, 9), mat(C.dc)); dc.position.set(0, 1.6, 0); dc.userData = { kind: 'dc' }; scene.add(dc); pickables.push(dc);
  dc.add(new THREE.LineSegments(new THREE.EdgesGeometry(dc.geometry), new THREE.LineBasicMaterial({ color: C.dcEdge })));
  const dryUnits = [], fans = [];
  for (let i = 0; i < 10; i++) {
    const u = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.5, 1.2), mat(C.dryFill)); u.position.set(-6.5 + i * 1.45, 3.45, -2.6); u.userData = { kind: 'dry' }; scene.add(u); dryUnits.push(u); pickables.push(u);
    const f = new THREE.Mesh(new THREE.RingGeometry(0.1, 0.42, 3), new THREE.MeshBasicMaterial({ color: C.dry, side: THREE.DoubleSide })); f.rotateX(-Math.PI / 2); f.position.set(u.position.x, 3.71, u.position.z); scene.add(f); fans.push(f);
  }
  const towers = [], plumes = [], dot = softDot();
  for (let i = 0; i < 4; i++) {
    const tw = new THREE.Mesh(new THREE.CylinderGeometry(0.9, 1.15, 2.8, 24), mat(C.towerFill)); tw.position.set(11 + i * 2.6, 1.4, 3.5); tw.userData = { kind: 'tower' }; scene.add(tw); towers.push(tw); pickables.push(tw);
    const np = 50, pos = new Float32Array(np * 3), seed = new Float32Array(np); for (let k = 0; k < np; k++) seed[k] = Math.random();
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: C.water, size: 0.8, map: dot, transparent: true, opacity: 0, depthWrite: false }));
    pts.position.copy(tw.position); pts.position.y = 2.8; pts.userData = { seed, n: np }; scene.add(pts); plumes.push(pts);
  }
  const pitRim = new THREE.Mesh(new THREE.TorusGeometry(3.4, 0.16, 8, 64), mat(C.rim)); pitRim.rotateX(Math.PI / 2); pitRim.position.set(-11, 0.1, 7); scene.add(pitRim);
  const pitBase = new THREE.Mesh(new THREE.CircleGeometry(3.3, 64), mat(C.storeFill)); pitBase.rotateX(-Math.PI / 2); pitBase.position.set(-11, 0.03, 7); scene.add(pitBase);
  const pitFill = new THREE.Mesh(new THREE.CylinderGeometry(3.2, 3.2, 1, 64), mat(0x7f9fc4, { roughness: 0.7 })); pitFill.position.set(-11, -0.5, 7); pitFill.userData = { kind: 'pit' }; scene.add(pitFill); pickables.push(pitFill);
  const baseColor = { dc: C.dc, dry: C.dryFill, tower: C.towerFill, pit: 0x7f9fc4, cand: C.cand };

  // HTML labels: only the ones that carry meaning
  const labelLayer = document.createElement('div'); labelLayer.className = 'labels'; wrap.appendChild(labelLayer);
  const mkLabel = (text, pos, cls = '') => { const el = document.createElement('div'); el.className = 'lbl ' + cls; el.textContent = text; labelLayer.appendChild(el); return { el, pos }; };
  const lk = W(-1200, 1800);
  const labels = [mkLabel('data center · 100 MW IT', new THREE.Vector3(0, 4.6, 0)), mkLabel('Cayuga Lake', new THREE.Vector3(lk[0], 0.6, lk[1]))];
  cands.slice(0, 3).forEach(c => { const [x, z] = W(c.x, c.y); labels.push(mkLabel(c.name, new THREE.Vector3(x, (c.h || 8) * S * HX + 1, z), 'small')); });
  const scenarioLbl = mkLabel('', new THREE.Vector3(0, 8, 0), 'scen'); scenarioLbl.el.style.display = 'none';
  const tipEl = document.createElement('div'); tipEl.className = 'lbl tip3d'; tipEl.style.display = 'none'; labelLayer.appendChild(tipEl);

  // state
  let rows = null, day = 0, playing = false, maxWater = 1, maxHeat = 1, t0 = performance.now(), drag = null, speed = 1, hovered = null, idle = 0;
  const cam = { yaw: -0.6, pitch: 0.45, dist: 110, target: new THREE.Vector3(-6, 0, -14) }, goal = { yaw: -0.6, pitch: 0.45, dist: 110, target: new THREE.Vector3(-6, 0, -14) };
  const PRESETS = { overview: { yaw: -0.6, pitch: 0.45, dist: 110, target: [-6, 0, -14] }, network: { yaw: -0.9, pitch: 0.8, dist: 360, target: [-40, 0, -120] }, towers: { yaw: 0.9, pitch: 0.3, dist: 34, target: [14, 1.5, 3.5] }, pit: { yaw: -0.3, pitch: 0.7, dist: 26, target: [-11, 0, 7] } };
  document.querySelectorAll('[data-cam]').forEach(b => b.onclick = () => { const p = PRESETS[b.dataset.cam]; if (!p) return; goal.yaw = p.yaw; goal.pitch = p.pitch; goal.dist = p.dist; goal.target.set(...p.target); idle = -8; document.querySelectorAll('[data-cam]').forEach(x => x.classList.toggle('on', x === b)); });
  const speedSel = document.getElementById('speed'); if (speedSel) speedSel.onchange = () => { speed = +speedSel.value; };
  const strip = document.getElementById('scen-strip');
  const slider = document.getElementById('day'), dayLabel = document.getElementById('day-label'), playBtn = document.getElementById('play');

  window.addEventListener('alpine:daily', e => {
    rows = e.detail.rows; maxWater = Math.max(1, ...rows.map(r => r.water_gal)); maxHeat = Math.max(1, ...rows.map(r => r.heat_available_mw));
    slider.max = rows.length - 1; setDay(Math.min(day, rows.length - 1));
    if (strip) strip.innerHTML = rows.map(r => `<i class="${r.scenario !== 'normal' ? 'sc' : ''}"></i>`).join('');
    st.textContent = `${e.detail.run.scenario.replace('_', ' ')} · ${n.toLocaleString()} real buildings (OpenStreetMap), Census shoreline · drag to orbit, scroll to zoom, hover a customer`;
  });
  slider.oninput = () => setDay(+slider.value);
  playBtn.onclick = () => { playing = !playing; playBtn.textContent = playing ? 'Pause' : 'Play'; };
  let acc = 0;
  function setDay(d) {
    day = d; slider.value = d;
    const date = new Date(2023, 0, 1 + d); dayLabel.textContent = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (!rows) return;
    const r = rows[d];
    window.dispatchEvent(new CustomEvent('alpine:day', { detail: { row: r, label: dayLabel.textContent, max: { water: maxWater, heat: maxHeat } } }));
    const on = r.scenario !== 'normal'; scenarioLbl.el.style.display = on ? '' : 'none'; if (on) scenarioLbl.el.textContent = r.scenario.replace('_', ' ');
  }

  // which customers are on today: biggest first, until the day's exported heat is covered (annual MWh -> a winter day)
  function litSet(sellMw) {
    const dayMwh = sellMw * 24, on = new Set(); let a = 0;
    for (let i = 0; i < cands.length && a < dayMwh; i++) { if (cands[i].x <= -50) continue; a += cands[i].mwh / 365 * 2.2; on.add(i); }
    return on;
  }

  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(-2, -2), pv = new THREE.Vector3();
  canvas.addEventListener('pointermove', e => { const b = canvas.getBoundingClientRect(); mouse.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1); });
  canvas.addEventListener('pointerleave', () => mouse.set(-2, -2));
  function describe(o) {
    const r = rows ? rows[day] : null; if (!r) return ''; const k = o.userData.kind;
    if (k === 'dc') return `data center · ${(r.heat_available_mw / 24).toFixed(0)} MW of heat captured today`;
    if (k === 'dry') return `dry coolers · ${(r.dry_mw / 24).toFixed(0)} MW rejected, no water`;
    if (k === 'tower') return `cooling towers · ${(r.heat_tower_mw / 24).toFixed(0)} MW · ${Math.round(r.water_gal).toLocaleString()} gal today`;
    if (k === 'pit') return `pit storage · ${Math.round(r.storage_soc * 100)}% full · ${(r.heat_stored_mw / 24).toFixed(1)} MW net`;
    if (k === 'cand') { const c = o.userData.c, on = o.userData.on; return `${c.name} · ${c.use || 'building'} · ${Math.round(c.mwh).toLocaleString()} MWh/yr${on ? ' · on the network today' : o.userData.reachable ? ' · on its own boiler' : ' · across the lake, not reachable'}`; }
    return '';
  }

  const cA = new THREE.Color(), cB = new THREE.Color(), accentC = new THREE.Color(C.accent);
  function frame() {
    const now = performance.now(), dt = Math.min(0.05, (now - t0) / 1000); t0 = now;
    if (playing && rows) { acc += dt * speed; if (acc > 0.12) { acc = 0; setDay((day + 1) % rows.length); } }
    const r = rows ? rows[day] : null;
    const sellMw = r ? r.heat_exported_mw / 24 : 0, dryF = r ? r.dry_mw / maxHeat : 0, towerF = r ? r.heat_tower_mw / maxHeat : 0, waterF = r ? r.water_gal / maxWater : 0;
    fans.forEach((f, i) => f.rotation.z += dt * (2 + 30 * dryF) * (i % 2 ? 1 : -1));
    dryUnits.forEach(u => { if (u !== hovered) u.material.color.setHex(C.dryFill).lerp(cB.setHex(C.dry), dryF * 0.8); });
    towers.forEach(tw => { if (tw !== hovered) tw.material.color.setHex(C.towerFill).lerp(cB.setHex(C.tower), towerF * 0.85); });
    plumes.forEach(pts => {
      const p = pts.geometry.attributes.position.array, { seed, n: np } = pts.userData, rise = 1.5 + 5 * waterF;
      for (let k = 0; k < np; k++) { const ph = (now / 2500 + seed[k]) % 1; const sp = 0.3 + ph * (0.6 + 1.2 * waterF); p[k * 3] = Math.sin(seed[k] * 40 + ph * 6) * sp; p[k * 3 + 1] = ph * rise; p[k * 3 + 2] = Math.cos(seed[k] * 40 + ph * 6) * sp; }
      pts.geometry.attributes.position.needsUpdate = true; pts.material.opacity = 0.02 + 0.3 * waterF; pts.material.size = 0.6 + 1.0 * waterF;
    });
    const soc = r ? r.storage_soc : 0.5; pitFill.scale.y = Math.max(0.02, soc) * 1.4; pitFill.position.y = -0.7 + pitFill.scale.y * 0.5;
    const lit = litSet(sellMw);
    pipes.forEach((pp, i) => {
      const on = lit.has(i), ud = pp.m.userData; ud.on = on;
      pp.segs.forEach(s => { s.material.opacity += ((on ? 0.9 : 0) - s.material.opacity) * 0.1; s.visible = s.material.opacity > 0.02; });
      ud.glow += ((on ? 1 : 0) - ud.glow) * 0.1;
      if (pp.m !== hovered) pp.m.material.color.setHex(C.cand).lerp(cA.setHex(C.sell), ud.glow * 0.75);
      pp.pulse.visible = on;
      if (on && pp.pts.length > 1) { pp.t = (pp.t + dt * 0.12) % 1; const L = pp.pts.length - 1, seg = Math.min(L - 1, Math.floor(pp.t * L)), f = pp.t * L - seg; pp.pulse.position.copy(pp.pts[seg]).lerp(pp.pts[seg + 1], f); }
    });

    idle += dt; if (!drag && idle > 4) goal.yaw += dt * 0.04;
    const kc = 1 - Math.exp(-dt * 4);
    cam.yaw += (goal.yaw - cam.yaw) * kc; cam.pitch += (goal.pitch - cam.pitch) * kc; cam.dist += (goal.dist - cam.dist) * kc; cam.target.lerp(goal.target, kc);
    camera.position.set(cam.target.x + Math.sin(cam.yaw) * Math.cos(cam.pitch) * cam.dist, cam.target.y + Math.sin(cam.pitch) * cam.dist, cam.target.z + Math.cos(cam.yaw) * Math.cos(cam.pitch) * cam.dist);
    camera.lookAt(cam.target);
    resize(); renderer.render(scene, camera);

    ray.setFromCamera(mouse, camera); const hit = ray.intersectObjects(pickables, false)[0], o = hit ? hit.object : null;
    if (o !== hovered) { if (hovered) hovered.material.color.setHex(baseColor[hovered.userData.kind] ?? C.cand); hovered = o; }
    if (hovered) { hovered.material.color.copy(accentC); tipEl.style.display = ''; tipEl.textContent = describe(hovered); place(tipEl, hit.point.clone().add(new THREE.Vector3(0, 1.2, 0))); canvas.style.cursor = 'pointer'; }
    else { tipEl.style.display = 'none'; canvas.style.cursor = drag ? 'grabbing' : 'grab'; }
    labels.forEach(l => place(l.el, l.pos, cam.dist)); if (scenarioLbl.el.style.display !== 'none') place(scenarioLbl.el, scenarioLbl.pos);
    requestAnimationFrame(frame);
  }
  function place(el, pos, dist) {
    pv.copy(pos).project(camera); const off = pv.z > 1 || (el.classList.contains('small') && dist > 260); el.style.opacity = off ? '0' : '';
    el.style.transform = `translate(-50%, -100%) translate(${((pv.x + 1) / 2 * canvas.clientWidth).toFixed(1)}px, ${((1 - pv.y) / 2 * canvas.clientHeight).toFixed(1)}px)`;
  }
  function resize() { const w = canvas.clientWidth, h = canvas.clientHeight, pr = renderer.getPixelRatio(); if (canvas.width !== Math.round(w * pr) || canvas.height !== Math.round(h * pr)) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); } }
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; idle = 0; });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointermove', e => { if (!drag) return; goal.yaw -= (e.clientX - drag.x) * 0.006; goal.pitch = Math.min(1.4, Math.max(0.12, goal.pitch + (e.clientY - drag.y) * 0.004)); drag = { x: e.clientX, y: e.clientY }; idle = 0; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); goal.dist = Math.min(700, Math.max(14, goal.dist * (1 + e.deltaY * 0.0012))); idle = 0; }, { passive: false });
  frame();

  function softDot() { const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d'); const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32); grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.45, 'rgba(255,255,255,0.4)'); grad.addColorStop(1, 'rgba(255,255,255,0)'); g.fillStyle = grad; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); }
}
