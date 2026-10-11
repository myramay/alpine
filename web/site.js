// Alpine site scene: one data center, where its heat goes, day by day. three.js from a CDN, no build step.
// Driven by the daily rows the page loads from /events (window event 'alpine:daily').
import * as THREE from 'three';

const canvas = document.getElementById('scene');
if (canvas) init();

function init() {
  const C = { sell: 0x34d399, store: 0x60a5fa, dry: 0xa1a1aa, tower: 0xf87171, water: 0x22d3ee, dc: 0x27272a, ground: 0x0f0f12, bld: 0x3f3f46, accent: 0xf2a93b };
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0a0d);
  scene.fog = new THREE.Fog(0x0a0a0d, 60, 140);
  const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 300);
  scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x1a1d26, 0.9));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(30, 50, 20); scene.add(sun);

  // ground + lake
  scene.add(new THREE.Mesh(new THREE.PlaneGeometry(400, 400), new THREE.MeshLambertMaterial({ color: C.ground })).rotateX(-Math.PI / 2));
  const lake = new THREE.Mesh(new THREE.CircleGeometry(60, 48), new THREE.MeshLambertMaterial({ color: 0x14314a }));
  lake.rotateX(-Math.PI / 2); lake.position.set(40, 0.02, 75); scene.add(lake);

  // data center
  const dc = new THREE.Mesh(new THREE.BoxGeometry(16, 4, 9), new THREE.MeshLambertMaterial({ color: C.dc }));
  dc.position.y = 2; scene.add(dc);
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(dc.geometry), new THREE.LineBasicMaterial({ color: 0x4a5061 }));
  dc.add(edge);

  // dry coolers on the roof: 8 units, fans spin with load
  const dryUnits = [], fans = [];
  for (let i = 0; i < 8; i++) {
    const u = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.6, 1.6), new THREE.MeshLambertMaterial({ color: C.dry }));
    u.position.set(-6 + i * 1.75, 4.3, -2.5); scene.add(u); dryUnits.push(u);
    const f = new THREE.Mesh(new THREE.RingGeometry(0.15, 0.6, 3), new THREE.MeshBasicMaterial({ color: 0x1a1d26, side: THREE.DoubleSide }));
    f.rotateX(-Math.PI / 2); f.position.set(u.position.x, 4.62, u.position.z); scene.add(f); fans.push(f);
  }

  // cooling towers (east side) with a plume whose size is the day's water
  const towers = [], plumes = [];
  for (let i = 0; i < 4; i++) {
    const tw = new THREE.Mesh(new THREE.CylinderGeometry(1.1, 1.4, 3.2, 16), new THREE.MeshLambertMaterial({ color: 0x5a3b3b }));
    tw.position.set(12 + i * 3.2, 1.6, 4); scene.add(tw); towers.push(tw);
    const n = 70, pos = new Float32Array(n * 3), seed = new Float32Array(n);
    for (let k = 0; k < n; k++) seed[k] = Math.random();
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0xdde6f0, size: 0.9, map: softDot(), transparent: true, opacity: 0.0, depthWrite: false, blending: THREE.AdditiveBlending }));
    pts.position.copy(tw.position); pts.position.y = 3.2; pts.userData = { seed, n }; scene.add(pts); plumes.push(pts);
  }

  // pit storage (south-west): a ring with a liquid level inside
  const pitRim = new THREE.Mesh(new THREE.TorusGeometry(4.2, 0.25, 8, 40), new THREE.MeshLambertMaterial({ color: 0x3a4152 }));
  pitRim.rotateX(Math.PI / 2); pitRim.position.set(-12, 0.1, 10); scene.add(pitRim);
  const pitFill = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 1, 40), new THREE.MeshLambertMaterial({ color: C.store, emissive: C.store, emissiveIntensity: 0.3 }));
  pitFill.position.set(-12, -0.5, 10); scene.add(pitFill);

  // buildings (west and north) and pipes from the data center
  const rng = mulberry(7), buildings = [], pipes = [];
  const pipeMat = () => new THREE.MeshLambertMaterial({ color: C.dry, emissive: C.sell, emissiveIntensity: 0 });
  for (let i = 0; i < 14; i++) {
    const ang = Math.PI * (0.55 + rng() * 0.9), r = 22 + rng() * 26;
    const x = Math.cos(ang) * r, z = -Math.abs(Math.sin(ang)) * r * 0.8 - 6;
    const h = 2 + rng() * 6, w = 2.5 + rng() * 3;
    const b = new THREE.Mesh(new THREE.BoxGeometry(w, h, w), new THREE.MeshLambertMaterial({ color: C.bld }));
    b.position.set(x, h / 2, z); scene.add(b); buildings.push(b);
    const from = new THREE.Vector3(-8, 0.4, 0), to = new THREE.Vector3(x, 0.4, z);
    const len = from.distanceTo(to);
    const p = new THREE.Mesh(new THREE.CylinderGeometry(0.16, 0.16, len, 6), pipeMat());
    p.position.copy(from).lerp(to, 0.5); p.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(from).normalize());
    scene.add(p); pipes.push({ mesh: p, from, to, pulse: [] });
    for (let k = 0; k < 3; k++) {
      const s = new THREE.Mesh(new THREE.SphereGeometry(0.28, 8, 8), new THREE.MeshBasicMaterial({ color: C.sell }));
      s.visible = false; scene.add(s); pipes[i].pulse.push({ mesh: s, t: k / 3 });
    }
  }

  // labels (sprites)
  const label = (text, x, y, z) => { const s = makeLabel(text); s.position.set(x, y, z); scene.add(s); return s; };
  label('data center · 100 MW IT', -4, 6.5, 0); label('cooling towers', 16.8, 1.2, 9); label('pit storage', -12, 2.4, 10); label('heat network · 15 km', -30, 9, -30);
  const scenarioTag = label('', 0, 9.5, 0); scenarioTag.visible = false;

  // state
  let rows = null, day = 0, playing = false, maxWater = 1, maxHeat = 1, t0 = performance.now(), drag = null, yaw = -0.55, pitch = 0.42, dist = 62;
  const hud = document.getElementById('hud-stats'), slider = document.getElementById('day'), dayLabel = document.getElementById('day-label'), playBtn = document.getElementById('play');
  const st = document.getElementById('site-status');

  window.addEventListener('alpine:daily', e => {
    rows = e.detail.rows; maxWater = Math.max(1, ...rows.map(r => r.water_gal)); maxHeat = Math.max(1, ...rows.map(r => r.heat_available_mw));
    slider.max = rows.length - 1; setDay(Math.min(day, rows.length - 1));
    st.textContent = `${e.detail.run.scenario.replace('_', ' ')} · drag to orbit, scroll to zoom, press play to run the year`;
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
    scenarioTag.visible = r.scenario !== 'normal';
    if (scenarioTag.visible) { scene.remove(scenarioTag); const s = makeLabel(r.scenario.replace('_', ' ').toUpperCase(), '#f2a93b'); s.position.set(0, 9.5, 0); scenarioTag.geometry = s.geometry; scenarioTag.material = s.material; scene.add(scenarioTag); }
  }

  function frame() {
    const now = performance.now(), dt = (now - t0) / 1000; t0 = now;
    if (playing && rows) { acc += dt; if (acc > 0.12) { acc = 0; setDay((day + 1) % rows.length); } }
    const r = rows ? rows[day] : null;
    const sellF = r ? r.heat_exported_mw / r.heat_available_mw : 0, dryF = r ? r.dry_mw / (maxHeat) : 0, towerF = r ? r.heat_tower_mw / maxHeat : 0, waterF = r ? r.water_gal / maxWater : 0;
    fans.forEach((f, i) => f.rotation.z += dt * (2 + 30 * dryF) * (i % 2 ? 1 : -1));
    dryUnits.forEach(u => u.material.color.setHex(C.dry).lerp(new THREE.Color(0xd0d5e0), dryF * 0.7));
    towers.forEach(tw => tw.material.color.setHex(0x5a3b3b).lerp(new THREE.Color(C.tower), towerF));
    plumes.forEach(pts => {
      const p = pts.geometry.attributes.position.array, { seed, n } = pts.userData, rise = 2 + 10 * waterF;
      for (let k = 0; k < n; k++) { const ph = (now / 2500 + seed[k]) % 1; p[k * 3] = Math.sin(seed[k] * 40 + ph * 6) * (0.3 + ph * (0.6 + 1.2 * waterF)); p[k * 3 + 1] = ph * rise; p[k * 3 + 2] = Math.cos(seed[k] * 40 + ph * 6) * (0.3 + ph * (0.6 + 1.2 * waterF)); }
      pts.geometry.attributes.position.needsUpdate = true; pts.material.opacity = 0.03 + 0.28 * waterF; pts.material.size = 0.7 + 1.1 * waterF;
    });
    const soc = r ? r.storage_soc : 0.5; pitFill.scale.y = Math.max(0.02, soc) * 1.4; pitFill.position.y = -0.7 + pitFill.scale.y * 0.5;
    pipes.forEach((pp, i) => {
      const on = sellF > 0.02 && i < Math.ceil(sellF * pipes.length * 1.4);
      pp.mesh.material.emissiveIntensity += ((on ? 0.9 * Math.min(1, sellF * 2) : 0) - pp.mesh.material.emissiveIntensity) * 0.1;
      pp.pulse.forEach(pu => { pu.mesh.visible = on; if (on) { pu.t = (pu.t + dt * 0.35) % 1; pu.mesh.position.copy(pp.from).lerp(pp.to, pu.t); } });
    });
    if (!drag) yaw += dt * 0.05;
    camera.position.set(Math.sin(yaw) * Math.cos(pitch) * dist, Math.sin(pitch) * dist, Math.cos(yaw) * Math.cos(pitch) * dist);
    camera.lookAt(-8, 1, -10);
    resize(); renderer.render(scene, camera); requestAnimationFrame(frame);
  }
  function resize() { const w = canvas.clientWidth, h = canvas.clientHeight; if (canvas.width !== w * renderer.getPixelRatio() || canvas.height !== h * renderer.getPixelRatio()) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); } }
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointermove', e => { if (!drag) return; yaw -= (e.clientX - drag.x) * 0.006; pitch = Math.min(1.3, Math.max(0.12, pitch + (e.clientY - drag.y) * 0.004)); drag = { x: e.clientX, y: e.clientY }; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); dist = Math.min(140, Math.max(25, dist + e.deltaY * 0.05)); }, { passive: false });
  frame();

  function makeLabel(text, color = '#e8eaf0') {
    const c = document.createElement('canvas'), ctx = c.getContext('2d'); c.width = 512; c.height = 96;
    ctx.font = '600 34px system-ui, sans-serif'; ctx.fillStyle = color; ctx.textAlign = 'center'; ctx.fillText(text, 256, 60);
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthTest: false }));
    s.scale.set(16, 3, 1); return s;
  }
  function softDot() {
    const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32); grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(0.4, 'rgba(255,255,255,0.35)'); grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
  }
  function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
}
