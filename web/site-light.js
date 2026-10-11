// Alpine site scene, light theme: the site as an isometric diorama tile.
// A rounded ground slab (~1.3 km square) centred on the data center, with the real OpenStreetMap footprints
// and shoreline clipped to it, the cooling plant, and the RECOMMENDED heat-reuse plan from /design placed on
// free land next to the data center (greenhouse, fish farm, drying hall, short pipes; trucked users as routes
// leaving the tile). Alpine's daily rows drive what spins, steams, fills and flows.
// DOM contract: #scene, #site-status, #play, #speed, #day, #day-label, #scen-strip, [data-cam] (toggles .on);
// listens for 'alpine:daily', dispatches 'alpine:day'. Site: ?site=<id> (default lansing) -> geo/<id>.json.
// Scene units: 1 = 1 m. x east, z south (north = -z). Heights exaggerated.
import * as THREE from 'three';

const canvas = document.getElementById('scene');
if (canvas) init();

async function init() {
  const site = new URLSearchParams(location.search).get('site') || 'lansing';
  const R = 650, CORNER = 64, SLAB = 30, HX = 2.2;        // tile half-size, corner radius, slab thickness (m)
  const C = {
    ground: '#f1f4f2', water: '#d5e3f3', waterDeep: '#bcd0e8', pad: '#eaedeb',
    bld: 0xf7f7f5, slab: 0xeef1ef, dc: 0xfdfdfc, amber: 0xc98a2b,
    green: 0x377a53, greenFill: 0xcee7d7, blue: 0x426c9c, blueFill: 0xd7e5f5,
    red: 0xb33f3f, redFill: 0xf0d6d6, gray: 0x8a918d, grayFill: 0xe5e8e6, amberFill: 0xf3e4cc,
  };
  const st = document.getElementById('site-status');
  const wrap = canvas.parentElement;
  st.textContent = 'Loading the site…';

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 1, 8000);

  scene.add(new THREE.HemisphereLight(0xffffff, 0xd4dbd7, 1.95));
  const sun = new THREE.DirectionalLight(0xffffff, 1.45);
  sun.position.set(-700, 950, 220); sun.castShadow = true;
  Object.assign(sun.shadow.camera, { left: -R - 120, right: R + 120, top: R + 120, bottom: -R - 120, near: 10, far: 2600 });
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; sun.shadow.normalBias = 0.6; sun.shadow.radius = 4;
  scene.add(sun, sun.target);

  const mat = (color, extra = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.92, metalness: 0, ...extra });
  const pickables = [];

  // ---------- data ----------
  let geo;
  try { const r = await fetch(`geo/${encodeURIComponent(site)}.json`); if (!r.ok) throw new Error(r.status); geo = await r.json(); }
  catch (e) { st.textContent = `Could not load geo/${site}.json: ${e.message}`; return; }
  let plan = null;
  try { const r = await fetch(`/design?site=${encodeURIComponent(site)}`); if (r.ok) plan = await r.json(); } catch (e) { plan = null; }
  const anchors = (plan && Array.isArray(plan.anchors)) ? plan.anchors.filter(a => a && a.key) : [];
  const trucked = plan && plan.trucked && Array.isArray(plan.trucked.users) ? plan.trucked.users : [];

  // ---------- tile shape + occupancy ----------
  const inTile = (x, y, m = 0) => {
    const ax = Math.abs(x), ay = Math.abs(y), e = R - m, r = Math.max(1, CORNER - m);
    if (ax > e || ay > e) return false;
    if (ax > e - r && ay > e - r) return Math.hypot(ax - (e - r), ay - (e - r)) <= r;
    return true;
  };
  const tileShape = (() => {
    const s = new THREE.Shape(), e = R, r = CORNER;
    s.moveTo(-e + r, -e); s.lineTo(e - r, -e); s.quadraticCurveTo(e, -e, e, -e + r); s.lineTo(e, e - r); s.quadraticCurveTo(e, e, e - r, e);
    s.lineTo(-e + r, e); s.quadraticCurveTo(-e, e, -e, e - r); s.lineTo(-e, -e + r); s.quadraticCurveTo(-e, -e, -e + r, -e); return s;
  })();

  const N = 2048, k = N / (2 * R);
  const px = (x, y) => [(x + R) * k, (R - y) * k];
  const tex = document.createElement('canvas'); tex.width = tex.height = N;
  const g = tex.getContext('2d');
  const waterPath = new Path2D(); let hasWater = false;
  for (const poly of geo.water || []) {
    if (!poly || poly.length < 3) continue; hasWater = true;
    poly.forEach(([x, y], i) => { const [a, b] = px(x, y); i ? waterPath.lineTo(a, b) : waterPath.moveTo(a, b); }); waterPath.closePath();
  }

  const CELL = 10, GN = Math.round(2 * R / CELL), occ = new Uint8Array(GN * GN), wat = new Uint8Array(GN * GN);
  const cellXY = (i, j) => [-R + (i + 0.5) * CELL, -R + (j + 0.5) * CELL];
  for (let j = 0; j < GN; j++) for (let i = 0; i < GN; i++) {
    const [x, y] = cellXY(i, j), [a, b] = px(x, y);
    if (hasWater && g.isPointInPath(waterPath, a, b, 'nonzero')) { wat[j * GN + i] = 1; occ[j * GN + i] = 1; }
    if (!inTile(x, y, 24)) occ[j * GN + i] = 1;
  }
  const markRect = (x0, y0, x1, y1, v = 1, arr = occ) => {
    const i0 = Math.max(0, Math.floor((x0 + R) / CELL)), i1 = Math.min(GN - 1, Math.floor((x1 + R) / CELL));
    const j0 = Math.max(0, Math.floor((y0 + R) / CELL)), j1 = Math.min(GN - 1, Math.floor((y1 + R) / CELL));
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) arr[j * GN + i] = v;
  };
  // the cooling plant: data center hall, towers to the east, storage tank to the south
  const DC = { w: 170, d: 92, h: 26 };
  const COMPLEX = { x0: -100, y0: -150, x1: 150, y1: 66 };
  markRect(COMPLEX.x0 - 10, COMPLEX.y0 - 10, COMPLEX.x1 + 10, COMPLEX.y1 + 10);

  // buildings inside the tile (drop the old plant footprints under the complex), largest ~1500
  const area = p => { let s = 0; for (let i = 0; i < p.length; i++) { const [x1, y1] = p[i], [x2, y2] = p[(i + 1) % p.length]; s += x1 * y2 - x2 * y1; } return Math.abs(s / 2); };
  let blds = [];
  for (const b of geo.buildings || []) {
    if (!b.p || b.p.length < 3) continue;
    if (!b.p.every(([x, y]) => inTile(x, y, 14))) continue;
    let cx = 0, cy = 0; b.p.forEach(([x, y]) => { cx += x; cy += y; }); cx /= b.p.length; cy /= b.p.length;
    if (cx > COMPLEX.x0 - 25 && cx < COMPLEX.x1 + 25 && cy > COMPLEX.y0 - 25 && cy < COMPLEX.y1 + 25) continue;
    const xs = b.p.map(q => q[0]), ys = b.p.map(q => q[1]);
    blds.push({ b, a: area(b.p), box: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)] });
  }
  blds.sort((p, q) => q.a - p.a); blds = blds.slice(0, 1500);
  const occB = occ.slice(); blds.forEach(o => markRect(o.box[0] - 6, o.box[1] - 6, o.box[2] + 6, o.box[3] + 6, 1, occB));

  // place each plan item on the nearest free rectangle (summed-area table over the occupancy grid)
  const sat = arr => { const s = new Int32Array((GN + 1) * (GN + 1)); for (let j = 0; j < GN; j++) for (let i = 0; i < GN; i++) s[(j + 1) * (GN + 1) + i + 1] = arr[j * GN + i] + s[j * (GN + 1) + i + 1] + s[(j + 1) * (GN + 1) + i] - s[j * (GN + 1) + i]; return s; };
  const rectFree = (s, i0, j0, i1, j1) => s[(j1 + 1) * (GN + 1) + i1 + 1] - s[j0 * (GN + 1) + i1 + 1] - s[(j1 + 1) * (GN + 1) + i0] + s[j0 * (GN + 1) + i0] === 0;
  const gapTo = (x0, y0, x1, y1, B) => Math.hypot(Math.max(0, B.x0 - x1, x0 - B.x1), Math.max(0, B.y0 - y1, y0 - B.y1));
  function findSpot(w, d, arr) {
    const s = sat(arr), M = 2, best = { score: Infinity };
    for (const [ww, dd] of [[w, d], [d, w]]) {
      const ni = Math.ceil(ww / CELL) + 2 * M, nj = Math.ceil(dd / CELL) + 2 * M;
      for (let j0 = 0; j0 + nj <= GN; j0++) for (let i0 = 0; i0 + ni <= GN; i0++) {
        if (!rectFree(s, i0, j0, i0 + ni - 1, j0 + nj - 1)) continue;
        const x0 = -R + (i0 + M) * CELL, y0 = -R + (j0 + M) * CELL, x1 = x0 + ww, y1 = y0 + dd;
        const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
        // nearest to the plant; slight preference for the camera-facing side (south-east) so items aren't hidden
        const score = gapTo(x0, y0, x1, y1, COMPLEX) + 0.06 * Math.max(0, cy - cx);
        if (score < best.score) Object.assign(best, { score, x0, y0, x1, y1, w: ww, d: dd, cx, cy });
      }
    }
    return best.score < Infinity ? best : null;
  }
  const KIND = {
    greenhouse: { name: 'Greenhouse', pad: '#e3efe7' },
    aquaculture: { name: 'Fish farm', pad: '#e2eaf4' },
    drying: { name: 'Drying hall', pad: '#f2ebdf' },
  };
  const sized = anchors.map(a => {
    const mw = Math.max(0.5, +a.peak_mw || 1);
    let w, d;
    if (a.key === 'greenhouse') { const ar = Math.min(260000, (a.acres ? +a.acres : mw / 0.55) * 4047); w = Math.sqrt(ar * 1.35); d = ar / w; }
    else if (a.key === 'aquaculture') { w = 40 * Math.sqrt(mw); d = 34 * Math.sqrt(mw); }
    else if (a.key === 'drying') { w = 38 * Math.sqrt(mw); d = 17 * Math.sqrt(mw); }
    else { w = 30 * Math.sqrt(mw); d = 20 * Math.sqrt(mw); }
    return { a, w: Math.max(40, w), d: Math.max(28, d) };
  }).sort((p, q) => q.w * q.d - p.w * p.d);
  const placed = [], cleared = new Set();
  for (const s of sized) {
    let spot = null, f = 1;
    for (let tries = 0; tries < 6 && !spot; tries++, f *= 0.85) spot = findSpot(s.w * f, s.d * f, occB);
    if (!spot) { f = 1; for (let tries = 0; tries < 6 && !spot; tries++, f *= 0.85) spot = findSpot(s.w * f, s.d * f, occ); }   // dense town: clear buildings
    if (!spot) continue;
    markRect(spot.x0 - 14, spot.y0 - 14, spot.x1 + 14, spot.y1 + 14, 1, occ); markRect(spot.x0 - 14, spot.y0 - 14, spot.x1 + 14, spot.y1 + 14, 1, occB);
    blds.forEach((o, i) => { if (o.box[2] > spot.x0 - 8 && o.box[0] < spot.x1 + 8 && o.box[3] > spot.y0 - 8 && o.box[1] < spot.y1 + 8) cleared.add(i); });
    placed.push({ ...s, ...spot });
  }
  blds = blds.filter((o, i) => !cleared.has(i));

  // ---------- ground texture: land, water with a deeper shore band, pads ----------
  g.fillStyle = C.ground; g.fillRect(0, 0, N, N);
  g.save(); g.strokeStyle = '#e7ebe8'; g.lineWidth = 1.2;            // faint 100 m survey grid
  for (let v = -R + 50; v < R; v += 100) { const [a] = px(v, 0); g.beginPath(); g.moveTo(a, 0); g.lineTo(a, N); g.stroke(); g.beginPath(); g.moveTo(0, a); g.lineTo(N, a); g.stroke(); }
  g.restore();
  if (hasWater) {
    g.fillStyle = C.water; g.fill(waterPath, 'nonzero'); g.strokeStyle = C.water; g.lineWidth = 3; g.stroke(waterPath);
    const m = document.createElement('canvas'); m.width = m.height = N; const mg = m.getContext('2d');
    mg.fillStyle = C.waterDeep; mg.fillRect(0, 0, N, N); mg.globalCompositeOperation = 'destination-out'; mg.fill(waterPath, 'nonzero'); mg.stroke(waterPath);
    g.save(); g.clip(waterPath, 'nonzero'); g.filter = 'blur(22px)'; g.globalAlpha = 0.85; g.drawImage(m, 0, 0); g.filter = 'blur(1.2px)'; g.globalAlpha = 0.9; g.drawImage(m, 0, 0); g.restore();
  }
  const pad = (x0, y0, x1, y1, color, r = 10) => { const [a, b] = px(x0, y1), [c, d] = px(x1, y0); g.fillStyle = color; g.beginPath(); g.roundRect(a, b, c - a, d - b, r * k); g.fill(); };
  pad(COMPLEX.x0, COMPLEX.y0, COMPLEX.x1, COMPLEX.y1, C.pad, 14);
  placed.forEach(p => pad(p.x0 - 10, p.y0 - 10, p.x1 + 10, p.y1 + 10, (KIND[p.a.key] || {}).pad || C.pad, 12));
  const groundTex = new THREE.CanvasTexture(tex); groundTex.colorSpace = THREE.SRGBColorSpace; groundTex.anisotropy = 4;

  // ---------- the slab ----------
  const slabGeo = new THREE.ExtrudeGeometry(tileShape, { depth: SLAB, bevelEnabled: true, bevelSize: 3, bevelThickness: 3, bevelSegments: 3, curveSegments: 10 });
  slabGeo.rotateX(-Math.PI / 2); slabGeo.translate(0, -SLAB - 3.4, 0);
  const slab = new THREE.Mesh(slabGeo, mat(C.slab)); slab.receiveShadow = true; scene.add(slab);
  const topGeo = new THREE.ShapeGeometry(tileShape, 12);
  { const p = topGeo.attributes.position, uv = topGeo.attributes.uv; for (let i = 0; i < p.count; i++) uv.setXY(i, (p.getX(i) + R) / (2 * R), (p.getY(i) + R) / (2 * R)); }
  topGeo.rotateX(-Math.PI / 2);
  const top = new THREE.Mesh(topGeo, mat(0xffffff, { map: groundTex, roughness: 1 })); top.receiveShadow = true; scene.add(top);
  // soft drop shadow under the tile
  const blob = (() => { const c = document.createElement('canvas'); c.width = c.height = 256; const x = c.getContext('2d'); x.filter = 'blur(18px)'; x.fillStyle = 'rgba(30,40,35,0.16)'; x.beginPath(); x.roundRect(40, 40, 176, 176, 26); x.fill(); const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; })();
  const shadowPlane = new THREE.Mesh(new THREE.PlaneGeometry(2 * R * 1.45, 2 * R * 1.45), new THREE.MeshBasicMaterial({ map: blob, transparent: true, depthWrite: false }));
  shadowPlane.rotation.x = -Math.PI / 2; shadowPlane.position.set(40, -SLAB - 40, 40); scene.add(shadowPlane);

  // ---------- buildings: one merged mesh ----------
  function merge(parts) {
    let total = 0; parts.forEach(p => total += p.attributes.position.array.length);
    const pos = new Float32Array(total), nrm = new Float32Array(total); let o = 0;
    parts.forEach(p => { pos.set(p.attributes.position.array, o); nrm.set(p.attributes.normal.array, o); o += p.attributes.position.array.length; p.dispose(); });
    const bg = new THREE.BufferGeometry(); bg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); bg.setAttribute('normal', new THREE.BufferAttribute(nrm, 3)); return bg;
  }
  const flat = gm => { const q = gm.index ? gm.toNonIndexed() : gm; q.computeVertexNormals(); return q; };
  if (blds.length) {
    const parts = blds.map(({ b }) => flat(new THREE.ExtrudeGeometry(new THREE.Shape(b.p.map(([x, y]) => new THREE.Vector2(x, y))), { depth: Math.max(7, (b.h || 6) * HX), bevelEnabled: false, curveSegments: 1 })));
    const bg = merge(parts); bg.rotateX(-Math.PI / 2);
    const m = new THREE.Mesh(bg, mat(C.bld, { roughness: 1 })); m.castShadow = m.receiveShadow = true; scene.add(m);
  }

  // ---------- the plant ----------
  const shadowed = o => { o.traverse(c => { if (c.isMesh) { c.castShadow = true; c.receiveShadow = true; } }); return o; };
  const dc = shadowed(new THREE.Mesh(new THREE.BoxGeometry(DC.w, DC.h, DC.d), mat(C.dc)));
  dc.position.set(0, DC.h / 2, 0); dc.userData = { kind: 'dc' }; scene.add(dc); pickables.push(dc);
  dc.add(new THREE.LineSegments(new THREE.EdgesGeometry(dc.geometry), new THREE.LineBasicMaterial({ color: C.amber })));
  const dryMat = mat(C.grayFill), dryUnits = [], fans = [];
  const fanGeo = merge([flat(new THREE.BoxGeometry(9, 0.5, 1.4)), flat(new THREE.BoxGeometry(1.4, 0.5, 9))]);
  const fanMat = new THREE.MeshBasicMaterial({ color: 0xa9b1ac }), hubGeo = new THREE.CylinderGeometry(5.4, 5.4, 0.5, 28), hubMat = mat(0xd2d8d4);
  for (let r = 0; r < 2; r++) for (let i = 0; i < 8; i++) {
    const u = shadowed(new THREE.Mesh(new THREE.BoxGeometry(14, 4, 14), dryMat));
    u.position.set(-63 + i * 18, DC.h + 2, -18 + r * 30); u.userData = { kind: 'dry' }; scene.add(u); dryUnits.push(u); pickables.push(u);
    const hub = new THREE.Mesh(hubGeo, hubMat); hub.position.set(u.position.x, DC.h + 4.1, u.position.z); scene.add(hub);
    const f = new THREE.Mesh(fanGeo, fanMat); f.position.set(u.position.x, DC.h + 4.5, u.position.z); scene.add(f); fans.push(f);
  }
  const towers = [], plumes = [], dot = softDot();
  for (let i = 0; i < 4; i++) {
    const tw = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(9, 11.5, 24, 32), mat(C.redFill)));
    tw.position.set(122, 12, 40 - i * 26); tw.userData = { kind: 'tower' }; scene.add(tw); towers.push(tw); pickables.push(tw);
    const np = 36, pos = new Float32Array(np * 3), seed = new Float32Array(np); for (let q = 0; q < np; q++) seed[q] = Math.random();
    const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xbccad6, size: 14, map: dot, transparent: true, opacity: 0, depthWrite: false }));
    pts.position.set(tw.position.x, 24, tw.position.z); pts.userData = { seed, n: np }; pts.frustumCulled = false; scene.add(pts); plumes.push(pts);
  }
  const PIT = { x: -40, z: 108, r: 32, h: 12 };
  const pitWall = shadowed(new THREE.Mesh(new THREE.CylinderGeometry(PIT.r, PIT.r, PIT.h, 48, 1, true), mat(0xf4f6f8, { side: THREE.DoubleSide })));
  pitWall.position.set(PIT.x, PIT.h / 2, PIT.z); scene.add(pitWall);
  const pitRim = new THREE.Mesh(new THREE.TorusGeometry(PIT.r, 0.9, 6, 64), mat(0xdfe5ea)); pitRim.rotation.x = Math.PI / 2; pitRim.position.set(PIT.x, PIT.h, PIT.z); scene.add(pitRim);
  const pitFill = new THREE.Mesh(new THREE.CylinderGeometry(PIT.r - 1, PIT.r - 1, 1, 48), mat(0x9db8d8, { roughness: 0.5 }));
  pitFill.position.set(PIT.x, 0.5, PIT.z); pitFill.userData = { kind: 'pit' }; scene.add(pitFill); pickables.push(pitFill, pitWall); pitWall.userData = { kind: 'pit' };
  // heat shimmer above the hall
  const shimmer = (() => {
    const np = 50, pos = new Float32Array(np * 3), seed = new Float32Array(np); for (let q = 0; q < np; q++) seed[q] = Math.random();
    const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const pts = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xf0cf9e, size: 22, map: dot, transparent: true, opacity: 0, depthWrite: false }));
    pts.position.set(0, DC.h + 6, 0); pts.userData = { seed, n: np }; pts.frustumCulled = false; scene.add(pts); return pts;
  })();

  // ---------- the recommended plan ----------
  const items = [];
  const gable = (bw, eave, ridge, len) => { const s = new THREE.Shape(); s.moveTo(-bw / 2, 0); s.lineTo(bw / 2, 0); s.lineTo(bw / 2, eave); s.lineTo(0, eave + ridge); s.lineTo(-bw / 2, eave); s.closePath(); const gm = flat(new THREE.ExtrudeGeometry(s, { depth: len, bevelEnabled: false })); gm.translate(0, 0, -len / 2); return gm; };
  function buildItem(p) {
    const grp = new THREE.Group(); grp.position.set(p.cx, 0, -p.cy); let body, labelY = 20; const glow = [];
    if (p.a.key === 'greenhouse') {
      const nb = Math.max(3, Math.round(p.w / 46)), bw = p.w / nb, parts = [];
      for (let i = 0; i < nb; i++) { const gm = gable(bw, 9, 6, p.d); gm.translate(-p.w / 2 + bw * (i + 0.5), 0, 0); parts.push(gm); }
      const gm = merge(parts);
      const glass = mat(0xd6ecdf, { roughness: 0.25, transparent: true, opacity: 0.88, emissive: new THREE.Color(C.green), emissiveIntensity: 0 });
      body = new THREE.Mesh(gm, glass); glow.push(glass);
      body.add(new THREE.LineSegments(new THREE.EdgesGeometry(gm, 20), new THREE.LineBasicMaterial({ color: C.green, transparent: true, opacity: 0.35 })));
      labelY = 18;
    } else if (p.a.key === 'aquaculture') {
      const hallD = p.d * 0.52, hm = mat(0xfafbfc);
      body = new THREE.Mesh(new THREE.BoxGeometry(p.w, 12, hallD), hm); body.position.set(0, 6, -(p.d / 2 - hallD / 2));
      const roof = new THREE.Mesh(new THREE.BoxGeometry(p.w + 1, 1.2, hallD + 1), mat(C.blueFill)); roof.position.y = 6.6; body.add(roof);
      const yardD = p.d - hallD - 6, tr = Math.max(5, Math.min(9, yardD / 2.4)), nx = Math.max(2, Math.floor(p.w / (2 * tr + 7))), ny = Math.max(1, Math.floor(yardD / (2 * tr + 6)));
      const tankWall = mat(C.blueFill), waterM = mat(0x7ea2c9, { roughness: 0.35, emissive: new THREE.Color(C.blue), emissiveIntensity: 0 }); glow.push(waterM);
      for (let a = 0; a < nx; a++) for (let b = 0; b < ny; b++) {
        const x = -p.w / 2 + (a + 0.5) * p.w / nx, z = p.d / 2 - (b + 0.5) * yardD / ny;
        const t = new THREE.Mesh(new THREE.CylinderGeometry(tr, tr, 5, 28, 1, true), tankWall); t.position.set(x, 2.5, z); grp.add(t);
        const wtr = new THREE.Mesh(new THREE.CircleGeometry(tr - 0.6, 28), waterM); wtr.rotation.x = -Math.PI / 2; wtr.position.set(x, 4.2, z); grp.add(wtr);
      }
      labelY = 16;
    } else {
      const roofM = mat(p.a.key === 'drying' ? C.amberFill : C.grayFill, { emissive: new THREE.Color(C.amber), emissiveIntensity: 0 }); glow.push(roofM);
      body = new THREE.Mesh(new THREE.BoxGeometry(p.w, 13, p.d), mat(0xfbfaf7)); body.position.y = 6.5;
      const roof = new THREE.Mesh(gable(p.d, 0.1, 4, p.w), roofM); roof.rotation.y = Math.PI / 2; roof.position.y = 6.5; body.add(roof);
      labelY = 22;
    }
    body.userData = { kind: 'plan', a: p.a }; grp.add(body); pickables.push(body);
    shadowed(grp); scene.add(grp);
    return { p, grp, body, glow, labelY };
  }
  placed.forEach(p => items.push(buildItem(p)));

  // short pipes: L-shaped from the hall wall to the nearest edge of each item, with heat pulses
  const pipeMat = mat(C.green, { roughness: 0.6 }), pulseMat = new THREE.MeshBasicMaterial({ color: 0x5fbf86, transparent: true, opacity: 0.9 });
  const flows = [];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  items.forEach(it => {
    const p = it.p, hx = DC.w / 2, hy = DC.d / 2;
    const sx = clamp(p.cx, -hx + 10, hx - 10), sy = clamp(p.cy, -hy + 10, hy - 10);
    let start;   // leave the hall from the wall facing the item
    const dx = p.cx - sx, dy = p.cy - sy;
    start = Math.abs(dx) / hx > Math.abs(dy) / hy ? [Math.sign(dx) * hx, sy] : [sx, Math.sign(dy) * hy];
    const end = [clamp(start[0], p.x0 + 6, p.x1 - 6), clamp(start[1], p.y0 + 6, p.y1 - 6)];
    const ex = end[0] < p.x0 + 7 ? p.x0 : end[0] > p.x1 - 7 ? p.x1 : end[0], ey = end[1] < p.y0 + 7 ? p.y0 : end[1] > p.y1 - 7 ? p.y1 : end[1];
    const verticalFirst = Math.abs(start[1]) === hy;
    const corner = verticalFirst ? [start[0], ey] : [ex, start[1]];
    const pts = [start, corner, [ex, ey]].map(([x, y]) => new THREE.Vector3(x, 2.2, -y));
    const segs = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1], len = a.distanceTo(b); if (len < 1) continue;
      const s = new THREE.Mesh(new THREE.BoxGeometry(3.4, 2.4, len + 3.4), pipeMat); s.position.copy(a).lerp(b, 0.5); s.lookAt(b.x, s.position.y, b.z); s.castShadow = true; scene.add(s); segs.push([a, b, len]);
    }
    const L = segs.reduce((s, q) => s + q[2], 0), pulses = [];
    for (let q = 0; q < 3; q++) { const m = new THREE.Mesh(new THREE.SphereGeometry(3.4, 12, 8), pulseMat); scene.add(m); pulses.push({ m, t: q / 3 }); }
    flows.push({ segs, L, pulses });
  });
  const along = (f, t) => { let d = t * f.L; for (const [a, b, len] of f.segs) { if (d <= len) return a.clone().lerp(b, d / len); d -= len; } const s = f.segs[f.segs.length - 1]; return s ? s[1].clone() : new THREE.Vector3(); };

  // trucked users: dashed routes leaving the tile in their real compass direction
  const routes = [];
  if (trucked.length) {
    const byName = {}; (geo.candidates || []).forEach(c => { const key = (c.name || '').toLowerCase(); (byName[key] = byName[key] || []).push(c); });
    const hash = s => { let h = 0; for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) | 0; return h; };
    const groups = new Map();
    trucked.forEach(u => {
      const m = byName[(u.name || '').toLowerCase()];
      let ang;
      if (m && m.length) { const c = m.reduce((a, b) => Math.abs(Math.hypot(b.x, b.y) / 1000 - (u.road_km || 0) * 0.75) < Math.abs(Math.hypot(a.x, a.y) / 1000 - (u.road_km || 0) * 0.75) ? b : a); ang = Math.atan2(c.y, c.x); }
      else ang = (Math.abs(hash(u.name || 'x')) % 360) * Math.PI / 180;
      const b = Math.round(ang / (Math.PI / 6));
      if (!groups.has(b)) groups.set(b, { ang: 0, users: [] });
      const gr = groups.get(b); gr.users.push(u); gr.ang += ang;
    });
    const truckGeo = new THREE.BoxGeometry(11, 5, 5), truckMat = mat(C.amber);
    for (const gr of groups.values()) {
      const ang = gr.ang / gr.users.length, c = Math.cos(ang), s = Math.sin(ang);
      const t0 = Math.min((DC.w / 2 + 12) / Math.max(1e-3, Math.abs(c)), (DC.d / 2 + 12) / Math.max(1e-3, Math.abs(s)));
      let t1 = (R - 4) / Math.max(Math.abs(c), Math.abs(s)); while (t1 > t0 && !inTile(c * t1, s * t1, 4)) t1 -= 4;
      const a = new THREE.Vector3(c * t0, 0.8, -s * t0), b = new THREE.Vector3(c * t1, 0.8, -s * t1);
      const mid = a.clone().lerp(b, 0.5), nrm = new THREE.Vector3(-(b.z - a.z), 0, b.x - a.x).normalize().multiplyScalar(a.distanceTo(b) * 0.12);
      const curve = new THREE.QuadraticBezierCurve3(a, mid.add(nrm), b);
      const lg = new THREE.BufferGeometry().setFromPoints(curve.getPoints(60));
      const line = new THREE.Line(lg, new THREE.LineDashedMaterial({ color: 0x9aa29d, dashSize: 8, gapSize: 8, transparent: true, opacity: 0.6 })); line.computeLineDistances(); scene.add(line);
      const end = new THREE.Mesh(new THREE.CircleGeometry(4, 20), new THREE.MeshBasicMaterial({ color: 0x8a918d })); end.rotation.x = -Math.PI / 2; end.position.copy(b).setY(0.9); scene.add(end);
      const truck = new THREE.Mesh(truckGeo, truckMat); truck.castShadow = true; scene.add(truck);
      routes.push({ curve, truck, t: Math.random(), users: gr.users, end: b });
    }
  }

  // ---------- labels + legend ----------
  const labelLayer = document.createElement('div'); labelLayer.className = 'labels'; wrap.appendChild(labelLayer);
  const mkLabel = (text, pos, cls = '') => { const el = document.createElement('div'); el.className = 'lbl ' + cls; el.textContent = text; labelLayer.appendChild(el); return { el, pos }; };
  const labels = [];
  const dcLabel = mkLabel('Data center', new THREE.Vector3(0, DC.h + 14, 0)); labels.push(dcLabel);
  const fmtMW = v => (v >= 10 ? Math.round(v) : (+v).toFixed(1).replace(/\.0$/, ''));
  items.forEach(it => {
    const a = it.a || it.p.a, nm = (KIND[a.key] || {}).name || (a.label || a.key);
    const extra = a.key === 'greenhouse' && a.acres ? ` · ${Math.round(a.acres)} acres` : '';
    labels.push(mkLabel(`${nm} · ${fmtMW(a.peak_mw)} MW${extra}`, new THREE.Vector3(it.p.cx, it.labelY + 4, -it.p.cy)));
  });
  if (hasWater) {   // label the open water: the water cell inside the tile farthest from land (coarse)
    let best = null, bd = -1;
    for (let j = 3; j < GN - 3; j += 2) for (let i = 3; i < GN - 3; i += 2) {
      if (!wat[j * GN + i]) continue; const [x, y] = cellXY(i, j); if (!inTile(x, y, 90)) continue;
      let d = 0; while (d < 30) { d++; if (!wat[j * GN + Math.min(GN - 1, i + d)] || !wat[j * GN + Math.max(0, i - d)] || !wat[Math.min(GN - 1, j + d) * GN + i] || !wat[Math.max(0, j - d) * GN + i]) break; }
      const score = Math.min(d, 24) - Math.hypot(x, y) / 900; if (score > bd) { bd = score; best = [x, y]; }
    }
    if (best) labels.push(mkLabel(geo.water_name || (site === 'lansing' ? 'Cayuga Lake' : 'Water'), new THREE.Vector3(best[0], 2, -best[1]), 'small'));
  }
  if (routes.length) {
    const r0 = routes.reduce((a, b) => b.users.length > a.users.length ? b : a);
    const kms = trucked.map(u => u.road_km).filter(v => v != null);
    const rng = kms.length ? ` · ${Math.round(Math.min(...kms))}–${Math.round(Math.max(...kms))} km` : '';
    labels.push(mkLabel(`${trucked.length} trucked user${trucked.length === 1 ? '' : 's'}${rng}`, r0.end.clone().setY(6), 'small'));
  }
  const scenarioLbl = mkLabel('', new THREE.Vector3(0, DC.h + 52, 0), 'scen'); scenarioLbl.el.style.display = 'none';
  const tipEl = document.createElement('div'); tipEl.className = 'lbl tip3d'; tipEl.style.display = 'none'; labelLayer.appendChild(tipEl);

  const keys = wrap.querySelector('.keys');
  let sbBar = null, sbTxt = null;
  if (keys) {
    const has = k => items.some(it => it.p.a.key === k);
    const row = (sw, t) => `<span><i class="${sw}"></i>${t}</span>`;
    keys.innerHTML = `<b class="kt">${plan ? 'Recommended plan' : 'Site'}</b>` + [
      row('k-dc', 'Data center'),
      has('greenhouse') && row('k-gh', 'Greenhouse'), has('aquaculture') && row('k-fish', 'Fish farm'), has('drying') && row('k-dry', 'Drying hall'),
      items.length && row('k-pipe', 'Heat pipe'), routes.length && row('k-truck', 'Trucked heat'),
      row('k-tower', 'Cooling towers'), row('k-pit', 'Heat storage'), hasWater && row('k-water', 'Water'),
    ].filter(Boolean).join('') + `<div class="sb"><b></b><em></em></div>`;
    sbBar = keys.querySelector('.sb b'); sbTxt = keys.querySelector('.sb em');
  }

  // ---------- camera ----------
  const deg = Math.PI / 180;
  const fitH = () => { const asp = Math.max(0.6, canvas.clientWidth / Math.max(1, canvas.clientHeight)); return Math.max((R + 30) * Math.SQRT2 / asp, (R + 30) * Math.SQRT2 * Math.sin(35 * deg) + SLAB) * 1.04; };
  const box = (pts, pad) => { const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]); const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys); return { c: [(x0 + x1) / 2, (y0 + y1) / 2], h: Math.max(x1 - x0, y1 - y0) * 0.62 + pad }; };
  const planBox = box([[COMPLEX.x0, COMPLEX.y0], [COMPLEX.x1, COMPLEX.y1], ...items.flatMap(it => [[it.p.x0, it.p.y0], [it.p.x1, it.p.y1]])], 40);
  const PRESETS = {
    overview: () => ({ az: 45 * deg, el: 35 * deg, h: fitH(), t: [0, 0, 0] }),
    network: () => ({ az: 40 * deg, el: 38 * deg, h: planBox.h, t: [planBox.c[0], 0, -planBox.c[1]] }),
    towers: () => ({ az: 58 * deg, el: 30 * deg, h: 135, t: [95, 12, -5] }),
    pit: () => ({ az: 35 * deg, el: 33 * deg, h: 150, t: [10, 10, 25] }),
  };
  PRESETS.heat = PRESETS.network; PRESETS.site = PRESETS.pit; PRESETS.water = PRESETS.towers;
  const camNames = { overview: 'Overview', network: 'Heat reuse', heat: 'Heat reuse', towers: 'Cooling', water: 'Cooling', pit: 'Data center', site: 'Data center' };
  document.querySelectorAll('[data-cam]').forEach(b => { if (camNames[b.dataset.cam]) b.textContent = camNames[b.dataset.cam]; });
  const cam = { ...PRESETS.overview(), t: [0, 0, 0] };
  let tween = null, userZoomed = false, curPreset = 'overview';
  function goPreset(name) {
    const p = PRESETS[name]; if (!p) return; curPreset = name; userZoomed = false;
    const to = p(); let daz = to.az - cam.az; daz = Math.atan2(Math.sin(daz), Math.cos(daz));
    tween = { from: { ...cam, t: cam.t.slice() }, to: { ...to, az: cam.az + daz }, t: 0 };
  }
  document.querySelectorAll('[data-cam]').forEach(b => b.onclick = () => { goPreset(b.dataset.cam); document.querySelectorAll('[data-cam]').forEach(x => x.classList.toggle('on', x === b)); });

  // ---------- day state ----------
  const speedSel = document.getElementById('speed');
  const strip = document.getElementById('scen-strip');
  const slider = document.getElementById('day'), dayLabel = document.getElementById('day-label'), playBtn = document.getElementById('play');
  let rows = null, day = 0, playing = false, maxWater = 1, maxHeat = 1, t0 = performance.now(), speed = 1, hovered = null, drag = null;
  if (speedSel) speedSel.onchange = () => { speed = +speedSel.value; };
  const nB = blds.length;
  const baseStatus = () => `${nB.toLocaleString()} buildings (OpenStreetMap)${hasWater ? ', shoreline' : ''} · ${plan ? `recommended plan: ${items.length} on-site user${items.length === 1 ? '' : 's'}${trucked.length ? `, ${trucked.length} trucked` : ''}` : 'no plan available'} · drag to rotate, scroll to zoom`;
  st.textContent = baseStatus();
  window.addEventListener('alpine:daily', e => {
    rows = e.detail.rows; maxWater = Math.max(1, ...rows.map(r => r.water_gal)); maxHeat = Math.max(1, ...rows.map(r => r.heat_available_mw));
    slider.max = rows.length - 1; setDay(Math.min(day, rows.length - 1));
    if (strip) strip.innerHTML = rows.map(r => `<i class="${r.scenario !== 'normal' ? 'sc' : ''}"></i>`).join('');
    const sc = e.detail.run && e.detail.run.scenario ? e.detail.run.scenario.replace('_', ' ') + ' · ' : '';
    st.textContent = sc + baseStatus();
  });
  slider.oninput = () => setDay(+slider.value);
  playBtn.onclick = () => { playing = !playing; playBtn.textContent = playing ? 'Pause' : 'Play'; };
  function setDay(d) {
    day = d; slider.value = d;
    const date = new Date(2023, 0, 1 + d); dayLabel.textContent = date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (!rows) return;
    const r = rows[d];
    window.dispatchEvent(new CustomEvent('alpine:day', { detail: { row: r, label: dayLabel.textContent, max: { water: maxWater, heat: maxHeat } } }));
    const on = r.scenario !== 'normal'; scenarioLbl.el.style.display = on ? '' : 'none'; if (on) scenarioLbl.el.textContent = r.scenario.replace('_', ' ');
    if (r.it_kwh) dcLabel.el.textContent = `Data center · ${Math.round(r.it_kwh / 24000)} MW IT`;
  }

  // ---------- hover ----------
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(-2, -2), pv = new THREE.Vector3();
  canvas.addEventListener('pointermove', e => { const b = canvas.getBoundingClientRect(); mouse.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1); });
  canvas.addEventListener('pointerleave', () => mouse.set(-2, -2));
  const money = v => v == null ? '' : `$${(v / 1e6).toFixed(1)}M`;
  function describe(o) {
    const k = o.userData.kind;
    if (k === 'plan') { const a = o.userData.a; return `${a.label || a.key} · ${fmtMW(a.peak_mw)} MW peak${a.delivered_mwh ? ` · ${(a.delivered_mwh / 1000).toFixed(1)} GWh/yr` : ''}${a.temp_c ? ` at ${a.temp_c} °C` : ''}${a.npv_usd != null ? ` · NPV ${money(a.npv_usd)}` : ''}`; }
    const r = rows ? rows[day] : null; if (!r) return k === 'dc' ? 'data center' : '';
    if (k === 'dc') return `data center · ${(r.heat_available_mw / 24).toFixed(0)} MW of heat captured today · ${(r.heat_exported_mw / 24).toFixed(0)} MW reused`;
    if (k === 'dry') return `dry coolers · ${(r.dry_mw / 24).toFixed(0)} MW rejected, no water`;
    if (k === 'tower') return `cooling towers · ${(r.heat_tower_mw / 24).toFixed(0)} MW · ${Math.round(r.water_gal).toLocaleString()} gal today`;
    if (k === 'pit') return `heat storage · ${Math.round(r.storage_soc * 100)}% full · ${(r.heat_stored_mw / 24).toFixed(1)} MW net`;
    return '';
  }
  const hoverEm = new THREE.Color(C.amber), black = new THREE.Color(0);
  const setHover = (o, on) => { const m = o && o.material; if (m && m.emissive && !(o.userData.kind === 'plan')) { m.emissive.copy(on ? hoverEm : black); m.emissiveIntensity = on ? 0.18 : 1; } };

  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY }; tween = null; });
  window.addEventListener('pointerup', () => { drag = null; });
  window.addEventListener('pointermove', e => {
    if (!drag) return; cam.az -= (e.clientX - drag.x) * 0.005; cam.el = Math.min(62 * deg, Math.max(20 * deg, cam.el + (e.clientY - drag.y) * 0.004)); drag = { x: e.clientX, y: e.clientY };
  });
  canvas.addEventListener('wheel', e => { e.preventDefault(); tween = null; userZoomed = true; cam.h = Math.min(fitH() * 1.25, Math.max(50, cam.h * (1 + e.deltaY * 0.0012))); }, { passive: false });

  // ---------- frame ----------
  const cB = new THREE.Color(), ease = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  let acc = 0, lastSb = 0, lastW = 0;
  function frame() {
    const now = performance.now(), dt = Math.min(0.05, (now - t0) / 1000); t0 = now;
    if (playing && rows) { acc += dt * speed; if (acc > 0.12) { acc = 0; setDay((day + 1) % rows.length); } }
    const r = rows ? rows[day] : null;
    const dryF = r ? r.dry_mw / maxHeat : 0.3, towerF = r ? r.heat_tower_mw / maxHeat : 0, waterF = r ? r.water_gal / maxWater : 0;
    const pxm = canvas.clientHeight * renderer.getPixelRatio() / (2 * cam.h);
    const loadF = r ? r.heat_available_mw / maxHeat : 0.6, reuseF = r && r.heat_available_mw > 0 ? Math.min(1, r.heat_exported_mw / r.heat_available_mw) : 0.3;
    fans.forEach((f, i) => f.rotation.y += dt * (0.6 + 9 * dryF) * (i % 2 ? 1 : -1));
    dryMat.color.setHex(C.grayFill).lerp(cB.setHex(C.gray), dryF * 0.55);
    towers.forEach(tw => tw.material.color.setHex(C.redFill).lerp(cB.setHex(C.red), Math.min(1, towerF * 2.5) * 0.6));
    plumes.forEach(pts => {
      const p = pts.geometry.attributes.position.array, { seed, n: np } = pts.userData, rise = 20 + 70 * waterF;
      for (let q = 0; q < np; q++) { const ph = (now / 3200 + seed[q]) % 1, sp = 2 + ph * (6 + 14 * waterF); p[q * 3] = Math.sin(seed[q] * 40 + ph * 5) * sp + ph * 14; p[q * 3 + 1] = ph * rise; p[q * 3 + 2] = Math.cos(seed[q] * 40 + ph * 5) * sp; }
      pts.geometry.attributes.position.needsUpdate = true; pts.material.opacity = waterF > 0.01 ? 0.06 + 0.28 * waterF : 0; pts.material.size = (7 + 9 * waterF) * pxm;
    });
    { const p = shimmer.geometry.attributes.position.array, { seed, n: np } = shimmer.userData, hot = Math.max(0, loadF - 0.45) / 0.55;
      for (let q = 0; q < np; q++) { const ph = (now / 4200 + seed[q]) % 1; p[q * 3] = (seed[q] - 0.5) * DC.w * 0.85 + Math.sin(now / 900 + seed[q] * 30) * 3; p[q * 3 + 1] = ph * (18 + 40 * hot); p[q * 3 + 2] = (((seed[q] * 7.31) % 1) - 0.5) * DC.d * 0.7; }
      shimmer.geometry.attributes.position.needsUpdate = true; shimmer.material.opacity = 0.02 + 0.1 * hot * (1 - reuseF * 0.5); shimmer.material.size = 16 * pxm; }
    const soc = r ? r.storage_soc : 0.5, fh = Math.max(0.4, soc * (PIT.h - 1.5)); pitFill.scale.y = fh; pitFill.position.y = fh / 2;
    pitFill.material.color.setHex(0xc9d9ea).lerp(cB.setHex(0x6f93bd), soc);
    flows.forEach(f => f.pulses.forEach(pu => { pu.t = (pu.t + dt * (0.05 + 0.25 * reuseF) * 120 / Math.max(60, f.L)) % 1; pu.m.position.copy(along(f, pu.t)).setY(4.6); }));
    pulseMat.opacity = 0.35 + 0.6 * reuseF;
    items.forEach(it => it.glow.forEach(m => { m.emissiveIntensity = 0.04 + 0.16 * reuseF * (0.75 + 0.25 * Math.sin(now / 700)); }));
    routes.forEach(rt => { rt.t = (rt.t + dt * 0.05) % 1; const p = rt.curve.getPointAt(rt.t), q = rt.curve.getPointAt(Math.min(1, rt.t + 0.01)); rt.truck.position.copy(p).setY(3.6); rt.truck.lookAt(q.x, 3.6, q.z); rt.truck.rotateY(Math.PI / 2); });

    // camera
    if (tween) { tween.t = Math.min(1, tween.t + dt / 1.1); const e = ease(tween.t), A = tween.from, B = tween.to; cam.az = A.az + (B.az - A.az) * e; cam.el = A.el + (B.el - A.el) * e; cam.h = A.h + (B.h - A.h) * e; cam.t = A.t.map((v, i) => v + (B.t[i] - v) * e); if (tween.t >= 1) tween = null; }
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (w !== lastW && !tween && !userZoomed && curPreset === 'overview') cam.h = fitH();
    lastW = w;
    resize();
    const asp = w / Math.max(1, h), oy = 0.05;
    camera.left = -cam.h * asp; camera.right = cam.h * asp; camera.top = cam.h * (1 - oy); camera.bottom = -cam.h * (1 + oy); camera.updateProjectionMatrix();
    const D = 3000; camera.position.set(cam.t[0] + Math.sin(cam.az) * Math.cos(cam.el) * D, cam.t[1] + Math.sin(cam.el) * D, cam.t[2] + Math.cos(cam.az) * Math.cos(cam.el) * D);
    camera.lookAt(cam.t[0], cam.t[1], cam.t[2]);
    renderer.render(scene, camera);

    ray.setFromCamera(mouse, camera); const hit = ray.intersectObjects(pickables, false)[0], o = hit ? hit.object : null;
    if (o !== hovered) { setHover(hovered, false); hovered = o; setHover(hovered, true); }
    if (hovered) { const txt = describe(hovered); tipEl.style.display = txt ? '' : 'none'; tipEl.textContent = txt; place(tipEl, hit.point.clone().add(new THREE.Vector3(0, 8, 0))); canvas.style.cursor = 'pointer'; }
    else { tipEl.style.display = 'none'; canvas.style.cursor = drag ? 'grabbing' : 'grab'; }
    keysBox = null; if (keys && keys.offsetParent) { const a = keys.getBoundingClientRect(), c = canvas.getBoundingClientRect(); keysBox = { l: a.left - c.left - 4, r: a.right - c.left + 4, t: a.top - c.top - 4, b: a.bottom - c.top + 4 }; }
    boxes.length = 0; labels.forEach(l => place(l.el, l.pos, true)); if (scenarioLbl.el.style.display !== 'none') place(scenarioLbl.el, scenarioLbl.pos);
    // scale bar (true along the screen horizontal in an orthographic view)
    if (sbBar) {
      const pxPerM = h / (2 * cam.h), want = 90 / pxPerM, nice = [10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000].reduce((a, b) => b <= want ? b : a, 10);
      const bw = Math.round(nice * pxPerM); if (bw !== lastSb) { lastSb = bw; sbBar.style.width = bw + 'px'; sbTxt.textContent = nice >= 1000 ? `${nice / 1000} km` : `${nice} m`; }
    }
    requestAnimationFrame(frame);
  }
  const boxes = []; let keysBox = null;
  function place(el, pos, avoid) {
    pv.copy(pos).project(camera);
    let x = (pv.x + 1) / 2 * canvas.clientWidth, y = (1 - pv.y) / 2 * canvas.clientHeight;
    if (avoid) {
      const hw = (el.offsetWidth || 80) / 2; if (x - hw < 6 && x > -hw) x = hw + 6; else if (x + hw > canvas.clientWidth - 6 && x < canvas.clientWidth + hw) x = canvas.clientWidth - 6 - hw;   // nudge up until it clears the labels already placed this frame
      const w = el.offsetWidth || 80, h = el.offsetHeight || 20;
      const clash = yy => boxes.some(b => Math.abs(b.x - x) < (b.w + w) / 2 + 4 && Math.abs(b.y - yy) < (b.h + h) / 2 + 3);
      for (const k of [0, -1, 1, -2, 2, -3, 3]) { const yy = y + k * (h + 4); if (!clash(yy)) { y = yy; break; } }
      boxes.push({ x, y, w, h });
      if (keysBox && x + w / 2 > keysBox.l && x - w / 2 < keysBox.r && y > keysBox.t && y - h < keysBox.b) { el.style.opacity = '0'; return; }
    }
    const off = pv.z > 1 || x < 8 || x > canvas.clientWidth - 8 || y < 20 || y > canvas.clientHeight - 60;
    el.style.opacity = off ? '0' : '';
    el.style.transform = `translate(-50%, -100%) translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
  }
  function resize() { const w = canvas.clientWidth, h = canvas.clientHeight, pr = renderer.getPixelRatio(); if (w && h && (canvas.width !== Math.round(w * pr) || canvas.height !== Math.round(h * pr))) renderer.setSize(w, h, false); }
  frame();

  function softDot() { const c = document.createElement('canvas'); c.width = c.height = 64; const x = c.getContext('2d'); const gr = x.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(0.45, 'rgba(255,255,255,0.4)'); gr.addColorStop(1, 'rgba(255,255,255,0)'); x.fillStyle = gr; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c); }
}
