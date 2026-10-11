// Alpine site scene: one data center, where its heat goes, day by day. three.js (vendored), no build step.
// Driven by the daily rows the page loads from /events (window event 'alpine:daily').
import * as THREE from 'three';

const canvas = document.getElementById('scene');
if (canvas) init();

function init() {
  const C = { sell: 0x34d399, store: 0x60a5fa, dry: 0xa1a1aa, tower: 0xf87171, water: 0x22d3ee, dc: 0x2b2b32, ground: 0x0e0e12, bld: 0x2c2c33, accent: 0xf2a93b };
  const wrap = canvas.parentElement;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0a0d);
  scene.fog = new THREE.Fog(0x0a0a0d, 60, 140);
  const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 300);
  scene.add(new THREE.HemisphereLight(0xb8c6e0, 0x1a1c24, 1.1));
  const sun = new THREE.DirectionalLight(0xfff1dc, 1.6); sun.position.set(40, 60, 25); scene.add(sun);
  const fill = new THREE.DirectionalLight(0x6ea8fe, 0.35); fill.position.set(-40, 20, -30); scene.add(fill);
  const std = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.75, metalness: 0.15, ...extra });
  const pickables = [];

  // ground + lake
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(500, 500), std(C.ground, { roughness: 1 })).rotateX(-Math.PI / 2));
  const grid = new THREE.GridHelper(300, 60, 0x1e1e24, 0x16161b); grid.position.y = 0.01; scene.add(grid);
  const lake = new THREE.Mesh(new THREE.CircleGeometry(60, 64), std(0x0f2a40, { roughness: 0.25, metalness: 0.3 }));
  lake.rotateX(-Math.PI / 2); lake.position.set(45, 0.03, 80); scene.add(lake);

  // data center
  const dc = new THREE.Mesh(new THREE.BoxGeometry(16, 4, 9), std(C.dc));
  dc.position.y = 2; dc.userData = { kind: 'dc' }; scene.add(dc); pickables.push(dc);
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(dc.geometry), new THREE.LineBasicMaterial({ color: 0x3f3f46 }));
  dc.add(edge);

  // dry coolers on the roof: 8 units, fans spin with load
  const dryUnits = [], fans = [];
  for (let i = 0; i < 8; i++) {
    const u = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.6, 1.6), std(C.dry));
    u.position.set(-6 + i * 1.75, 4.3, -2.5); u.userData = { kind: 'dry' }; scene.add(u); dryUnits.push(u); pickables.push(u);
    const f = new THREE.Mesh(new THREE.RingGeometry(0.15, 0.6, 3), new THREE.MeshBasicMaterial({ color: 0x111115, side: THREE.DoubleSide }));
    f.rotateX(-Math.PI / 2); f.position.set(u.position.x, 4.62, u.position.z); scene.add(f); fans.push(f);
  }

  // cooling towers (east side) with a plume whose size is the day's water
  const towers = [], plumes = [];
  for (let i = 0; i < 4; i++) {
    const tw = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 1.4, 3.2, 20), std(0x4a2f2f));
    tw.position.set(12 + i * 3.2, 1.6, 4); tw.userData = { kind: 'tower' }; scene.add(tw); towers.push(tw); pickables.push(tw);
    const n = 70, pos = new Float32Array(n * 3), seed = new Float32Array(n);
    for (let k = 0; k < n; k++) seed[k] = Math.random();
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xdde6f0, size: 0.9, map: softDot(), transparent: true, opacity: 0.0, depthWrite: false, blending: THREE.AdditiveBlending }));
    pts.position.copy(tw.position); pts.position.y = 3.2; pts.userData = { seed, n }; scene.add(pts); plumes.push(pts);
  }

  // pit storage (south-west): a ring with a liquid level inside
  const pitRim = new THREE.Mesh(new THREE.TorusGeometry(4.2, 0.25, 8, 48), std(0x3f3f46));
  pitRim.rotateX(Math.PI / 2); pitRim.position.set(-12, 0.1, 10); scene.add(pitRim);
  const pitFill = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 1, 48), std(C.store, { emissive: C.store, emissiveIntensity: 0.35, roughness: 0.3 }));
  pitFill.position.set(-12, -0.5, 10); pitFill.userData = { kind: 'pit' }; scene.add(pitFill); pickables.push(pitFill);

  // buildings (west and north) and pipes from the data center
  const rng = mulberry(7), buildings = [], pipes = [];
  for (let i = 0; i < 14; i++) {
    const ang = Math.PI * (0.55 + rng() * 0.9), r = 22 + rng() * 26;
    const x = Math.cos(ang) * r, z = -Math.abs(Math.sin(ang)) * r * 0.8 - 6;
    const h = 2 + rng() * 6, w = 2.5 + rng() * 3;
    const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, w), std(C.bld, { emissive: C.sell, emissiveIntensity: 0 }));
    b.position.set(x, h / 2, z); b.userData = { kind: 'bld', i, share: 0 }; scene.add(b); buildings.push(b); pickables.push(b);
    const from = new THREE.Vector3(-8, 0.4, 0), to = new THREE.Vector3(x, 0.4, z);
    const len = from.distanceTo(to);
    const p = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, len, 8), std(C.dry, { emissive: C.sell, emissiveIntensity: 0 }));
    p.position.copy(from).lerp(to, 0.5); p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(from).normalize());
    scene.add(p); pipes.push({ mesh: p, from, to, pulse: [], b });
    for (let k = 0; k < 3; k++) {
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.28, 8, 8), new THREE.MeshBasicMaterial({ color: C.sell }));
      s.visible = false; scene.add(s); pipes[i].pulse.push({ mesh: s, t: k / 3 });
    }
  }

  const shares = buildings.map(() => 0.4 + rng() * 0.6), shareSum = shares.reduce((a, b) => a + b, 0);
  buildings.forEach((b, i) => b.userData.share = shares[i] / shareSum);

  // HTML labels, projected each frame
  const labelLayer = document.createElement('div'); labelLayer.className = 'labels'; wrap.appendChild(labelLayer);
  const mkLabel = (text, pos, cls = '') => { const el = document.createElement('div'); el.className = 'lbl ' + cls; el.textContent = text; labelLayer.appendChild(el); return { el, pos }; };
  const labels = [mkLabel('data center · 100 MW IT', new THREE.Vector3(-2, 5.2, 0)), mkLabel('cooling towers', new THREE.Vector3(16.8, 4.2, 4)), mkLabel('pit storage · 2,800 MWh', new THREE.Vector3(-12, 1.6, 10)), mkLabel('heat network · 15 km', new THREE.Vector3(-30, 9, -30))];
  const scenarioLbl = mkLabel('', new THREE.Vector3(0, 8.5, 0), 'scen'); scenarioLbl.el.style.display = 'none';
  const tipEl = document.createElement('div'); tipEl.className = 'lbl tip3d'; tipEl.style.display = 'none'; labelLayer.appendChild(tipEl);

  // state
  let rows = null, day = 0, playing = false, maxWater = 1, maxHeat = 1, t0 = performance.now(), drag = null, speed = 1, hovered = null, idle = 0;
  const cam = { yaw: -0.55, pitch: 0.42, dist: 62, target: new THREE.Vector3(-8, 1, -10) }, goal = { yaw: -0.55, pitch: 0.42, dist: 62, target: new THREE.Vector3(-8, 1, -10) };
  const PRESETS = { overview: { yaw: -0.55, pitch: 0.42, dist: 62, target: [-8, 1, -10] }, network: { yaw: -1.1, pitch: 0.55, dist: 70, target: [-22, 1, -22] }, towers: { yaw: 0.9, pitch: 0.3, dist: 30, target: [17, 2, 4] }, pit: { yaw: -0.3, pitch: 0.7, dist: 28, target: [-12, 0, 10] } };
  document.querySelectorAll('[data-cam]').forEach(b => b.onclick = () => { const p = PRESETS[b.dataset.cam]; goal.yaw = p.yaw; goal.pitch = p.pitch; goal.dist = p.dist; goal.target.set(...p.target); idle = -8; document.querySelectorAll('[data-cam]').forEach(x => x.classList.toggle('on', x === b)); });
  const speedSel = document.getElementById('speed'); if (speedSel) speedSel.onchange = () => { speed = +speedSel.value; };
  const strip = document.getElementById('scen-strip');
  const hud = document.getElementById('hud-stats'), slider = document.getElementById('day'), dayLabel = document.getElementById('day-label'), playBtn = document.getElementById('play');
  const st = document.getElementById('site-status');

  window.addEventListener('alpine:daily', e => {
    rows = e.detail.rows; maxWater = Math.max(1, ...rows.map(r => r.water_gal)); maxHeat = Math.max(1, ...rows.map(r => r.heat_available_mw));
    slider.max = rows.length - 1; setDay(Math.min(day, rows.length - 1));
    if (strip) strip.innerHTML = rows.map(r => `<i class="${r.scenario !== 'normal' ? 'sc' : ''}"></i>`).join('');
    st.textContent = `${e.detail.run.scenario.replace('_', ' ')} · drag to orbit, scroll to zoom, hover anything, press play to run the year`;
  });
  slider.oninput = () => setDay(+slider.value);
  playBtn.onclick = () => { playing = !playing; playBtn.textContent = playing ? 'Pause' : 'Play'; };
  let acc = 0;

  function setDay(d) {
    day = d; slider.value = d;
    const date = new Date(2023, 0, 1 + d); dayLabel.textContent = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (!rows) return;
    const r = rows[d];
    const sell = r.heat_exported_mw / 24, dry = r.dry_mw / 24, tower = r.heat_tower_mw / 24, avail = r.heat_available_mw / 24, store = r.heat_stored_mw / 24;
    window.dispatchEvent(new CustomEvent('alpine:day', { detail: { row: r, label: dayLabel.textContent, max: { water: maxWater, heat: maxHeat } } }));
    hud.innerHTML = `<span>heat <b>${avail.toFixed(0)} MW</b></span><span>to network <b>${sell.toFixed(0)} MW</b></span><span>dry <b>${dry.toFixed(0)} MW</b></span><span>towers <b>${tower.toFixed(0)} MW</b></span><span>water <b>${Math.round(r.water_gal).toLocaleString()} gal</b></span><span>pit <b>${Math.round(r.storage_soc * 100)}%</b></span><span>cost <b>$${Math.round(r.cost_usd / 1000)}k</b></span>`;
    const on = r.scenario !== 'normal'; scenarioLbl.el.style.display = on ? '' : 'none'; if (on) scenarioLbl.el.textContent = r.scenario.replace('_', ' ');
  }

  function frame() {
    const now = performance.now(), dt = (now - t0) / 1000; t0 = now;
    if (playing && rows) { acc += dt * speed; if (acc > 0.12) { acc = 0; setDay((day + 1) % rows.length); } }
    const r = rows ? rows[day] : null;
    const sellF = r ? r.heat_exported_mw / r.heat_available_mw : 0, dryF = r ? r.dry_mw / (maxHeat) : 0, towerF = r ? r.heat_tower_mw / maxHeat : 0, waterF = r ? r.water_gal / maxWater : 0;
    fans.forEach((f, i) => f.rotation.z += dt * (2 + 30 * dryF) * (i % 2 ? 1 : -1));
    dryUnits.forEach(u => u.material.color.setHex(C.dry).lerp(new THREE.Color(0xe4e4e7), dryF * 0.6));
    towers.forEach(tw => tw.material.color.setHex(0x4a2f2f).lerp(new THREE.Color(C.tower), towerF * 0.9));
    plumes.forEach(pts => {
      const p = pts.geometry.attributes.position.array, { seed, n } = pts.userData, rise = 2 + 10 * waterF;
      for (let k = 0; k < n; k++) { const ph = (now / 2500 + seed[k]) % 1; p[k * 3] = Math.sin(seed[k] * 40 + ph * 6) * (0.3 + ph * (0.6 + 1.2 * waterF)); p[k * 3 + 1] = ph * rise; p[k * 3 + 2] = Math.cos(seed[k] * 40 + ph * 6) * (0.3 + ph * (0.6 + 1.2 * waterF)); }
      pts.geometry.attributes.position.needsUpdate = true; pts.material.opacity = 0.03 + 0.28 * waterF; pts.material.size = 0.7 + 1.1 * waterF;
    });
    const soc = r ? r.storage_soc : 0.5; pitFill.scale.y = Math.max(0.02, soc) * 1.4; pitFill.position.y = -0.7 + pitFill.scale.y * 0.5;
    pipes.forEach((pp, i) => {
      const on = sellF > 0.02 && i < Math.ceil(sellF * pipes.length * 1.4);
      pp.mesh.material.emissiveIntensity += ((on ? 1.2 * Math.min(1, sellF * 2) : 0) - pp.mesh.material.emissiveIntensity) * 0.1;
      if (pp.b !== hovered) pp.b.material.emissiveIntensity += ((on ? 0.06 : 0) - pp.b.material.emissiveIntensity) * 0.1;
      pp.pulse.forEach(pu => { pu.mesh.visible = on; if (on) { pu.t = (pu.t + dt * 0.35) % 1; pu.mesh.position.copy(pp.from).lerp(pp.to, pu.t); } });
    });
    idle += dt; if (!drag && idle > 4) goal.yaw += dt * 0.05;
    const kc = 1 - Math.exp(-dt * 5);
    cam.yaw += (goal.yaw - cam.yaw) * kc; cam.pitch += (goal.pitch - cam.pitch) * kc; cam.dist += (goal.dist - cam.dist) * kc; cam.target.lerp(goal.target, kc);
    camera.position.set(cam.target.x + Math.sin(cam.yaw) * Math.cos(cam.pitch) * cam.dist, cam.target.y + Math.sin(cam.pitch) * cam.dist, cam.target.z + Math.cos(cam.yaw) * Math.cos(cam.pitch) * cam.dist);
    camera.lookAt(cam.target);
    resize(); renderer.render(scene, camera);
    ray.setFromCamera(mouse, camera); const hit = ray.intersectObjects(pickables, false)[0], o = hit ? hit.object : null;
    if (o !== hovered) { if (hovered) { hovered.material.emissiveIntensity = hovered.userData.kind === 'pit' ? 0.35 : 0; if (hovered.userData.kind !== 'bld') hovered.material.emissive.setHex(hovered.userData.kind === 'pit' ? C.store : 0x000000); } hovered = o; }
    if (hovered) { hovered.material.emissive.setHex(hovered.userData.kind === 'bld' ? C.sell : C.accent); hovered.material.emissiveIntensity = 0.5; tipEl.style.display = ''; tipEl.textContent = describe(hovered); place(tipEl, hit.point.clone().add(new THREE.Vector3(0, 1.5, 0))); canvas.style.cursor = 'pointer'; }
    else { tipEl.style.display = 'none'; canvas.style.cursor = drag ? 'grabbing' : 'grab'; }
    labels.forEach(l => place(l.el, l.pos)); if (scenarioLbl.el.style.display !== 'none') place(scenarioLbl.el, scenarioLbl.pos);
    requestAnimationFrame(frame);
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
    if (k === 'bld') { const mw = r.heat_exported_mw / 24 * o.userData.share; return `building ${o.userData.i + 1} · ${mw.toFixed(1)} MW from the network${mw < 0.05 ? ' · on its own boiler' : ''}`; }
    return '';
  }
  function place(el, pos) {
    pv.copy(pos).project(camera); el.style.opacity = pv.z > 1 ? '0' : '';
    el.style.transform = `translate(-50%, -100%) translate(${((pv.x + 1) / 2 * canvas.clientWidth).toFixed(1)}px, ${((1 - pv.y) / 2 * canvas.clientHeight).toFixed(1)}px)`;
  }
  function resize() { const w = canvas.clientWidth, h = canvas.clientHeight; if (canvas.width !== w * renderer.getPixelRatio() || canvas.height !== h * renderer.getPixelRatio()) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); } }
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; idle = 0; });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointermove', e => { if (!drag) return; goal.yaw -= (e.clientX - drag.x) * 0.006; goal.pitch = Math.min(1.3, Math.max(0.12, goal.pitch + (e.clientY - drag.y) * 0.004)); drag = { x: e.clientX, y: e.clientY }; idle = 0; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); goal.dist = Math.min(150, Math.max(18, goal.dist + e.deltaY * 0.05)); idle = 0; }, { passive: false });
  frame();

  function softDot() {
    const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32); grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.4, 'rgba(255,255,255,0.35)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
  }
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
}
