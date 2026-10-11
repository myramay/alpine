// Mounts the 21st.dev Contribution Skyline (kedhareswer) on Alpine's daily rows. Red palette.
(() => {
  const root = document.getElementById('sky-root'), sel = document.getElementById('sky-metric'); if (!root) return;
  const METRICS = {
    water_gal:        { unit: 'gal',  plural: 'gal',  title: v => `${v.toLocaleString()} gallons of water this year` , scale: 1 },
    heat_exported_mw: { unit: 'MWh',  plural: 'MWh',  title: v => `${v.toLocaleString()} MWh of heat sold this year`, scale: 1 },
    heat_tower_mw:    { unit: 'MWh',  plural: 'MWh',  title: v => `${v.toLocaleString()} MWh rejected through towers this year`, scale: 1 },
    cost_usd:         { unit: '$',    plural: '$',    title: v => `$${v.toLocaleString()} net cost this year`, scale: 1 },
  };
  const RED = { dark: ['#3f0f0f', '#7f1d1d', '#b91c1c', '#ef4444'], light: ['#fecaca', '#f87171', '#dc2626', '#7f1d1d'] };
  let rows = null, metric = 'heat_exported_mw';
  const mount = () => {
    if (!rows || !window.mountSkyline) return;
    const m = METRICS[metric];
    const data = rows.map((r, i) => ({ date: new Date(Date.UTC(2023, 0, 1 + r.day)).toISOString().slice(0, 10), count: Math.max(0, Math.round(+r[metric])) }));
    const total = data.reduce((a, d) => a + d.count, 0);
    window.mountSkyline(root, { data, endDate: '2023-12-31', palette: RED, unit: m.unit, unitPlural: m.plural, title: m.title(total), defaultView: '3d', heightScale: 1 });
  };
  window.addEventListener('alpine:daily', e => { rows = e.detail.rows; mount(); });
  sel.onchange = () => { metric = sel.value; mount(); };
  if (document.readyState === 'complete') mount(); else window.addEventListener('load', mount);
})();
