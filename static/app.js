// ---- shared Chart.js styling ----
Chart.defaults.color = "#97a3b0";
Chart.defaults.font.family = "Space Mono, monospace";
Chart.defaults.borderColor = "#263039";

const ACCENT = "#45c4b0";
const WARN = "#f9a825";

// multi-pollutant state
let CHANNELS = [];
let curMetric = "pm25";
let curDays = 30;
let curArea = "";       // forecast area (empty -> primary node)
let curFcHours = 24;    // forecast horizon

async function getJSON(url) {
  try { const r = await fetch(url); return await r.json(); }
  catch (e) { return { error: String(e) }; }
}
const fmt = (n) => (n === null || n === undefined || Number.isNaN(n)) ? "—" : n;
const el = (id) => document.getElementById(id);
function setText(id, v) { const n = el(id); if (n) n.textContent = v; }

// ====================================================================
// SUMMARY (current conditions)
// ====================================================================
function renderSummary(s) {
  if (!s || s.error) { setText("aqiCategory", (s && s.error) || "No data"); return; }
  setText("updated", (s.live ? "live · " : "snapshot · ") + s.latest_time);
  setText("aqiValue", fmt(s.aqi));
  setText("pm25", fmt(s.pm25));
  setText("latestTime", s.latest_time);
  setText("aqiCategory", s.category.label);
  document.documentElement.style.setProperty("--cat", s.category.color);
  el("aqiOrb").style.setProperty("--cat", s.category.color);
}

// ====================================================================
// LIVE MULTI-POLLUTANT MONITORING
// ====================================================================
function renderLive(channels, now, latestTime) {
  CHANNELS = channels || [];
  if (latestTime) setText("liveTime", "as of " + latestTime);
  el("live").innerHTML = CHANNELS.map((ch) => {
    const v = now && now[ch.key] != null ? now[ch.key] : "—";
    return `<div class="tile" style="--c:${ch.color}">
      <div class="tile-top"><span class="tile-label">${ch.label}</span></div>
      <div class="tile-val">${v}<span class="tile-unit">${ch.unit}</span></div>
    </div>`;
  }).join("");
}

// ====================================================================
// CITY SENSOR NETWORK (multiple IoT nodes, multi-pollutant)
// ====================================================================
let netChart, mapObj, NET = null, netMetric = "pm25";

// one pollutant/environment reading -> a labelled chip (with its sub-AQI if any)
function readingChips(readings) {
  return (readings || []).map((r) => {
    const sub = r.aqi != null ? `<span class="rd-aqi">AQI ${r.aqi}</span>` : "";
    const v = r.value ?? "—";
    return `<span class="rd rd-${r.kind}" title="${r.label}${r.aqi != null ? " · AQI " + r.aqi : ""}">
      <span class="rd-label">${r.label}</span>
      <span class="rd-val">${v}<span class="rd-unit">${r.unit || ""}</span></span>${sub}
    </span>`;
  }).join("");
}

function popupReadings(readings) {
  return (readings || []).map((r) =>
    `${r.label} ${r.value ?? "—"} ${r.unit || ""}${r.aqi != null ? ` · AQI ${r.aqi}` : ""}`
  ).join("<br>");
}

function renderNetwork(net) {
  if (!net || net.error) { setText("netCount", "unavailable"); return; }
  NET = net;
  setText("netCount", `${net.node_count} ${net.live === false ? "stations" : "live stations"}`);
  setText("netUpdated", (net.live === false ? "snapshot · " : "live · ") + net.generated_at);

  // node cards (worst AQI first, server-sorted) — every channel the node reports
  el("nodes").innerHTML = (net.nodes || []).map((n) => {
    const nCh = (n.readings || []).length;
    return `
    <div class="node" style="--c:${n.category.color}">
      <div class="node-head">
        <span class="node-area">${n.area}${n.primary ? '<span class="node-pri">primary</span>' : ""}</span>
        <span class="node-aqi" style="color:${n.category.color}">${n.aqi ?? "—"}</span>
      </div>
      <div class="node-cat">${n.category.label}${n.dominant ? ` · <span class="node-dom">${n.dominant} driving</span>` : ""}</div>
      <div class="node-readings">${readingChips(n.readings)}</div>
      <div class="node-foot">
        <span>${nCh} channel${nCh === 1 ? "" : "s"}</span>
        <span class="node-time">${n.latest_time}</span>
      </div>
    </div>`;
  }).join("");

  drawNetworkMap(net.nodes || []);
  buildNetMetricToggle();
  // default to the worst-driving pollutant if PM2.5 isn't the comparison's pick
  const comps = net.comparisons || (net.comparison ? { pm25: net.comparison } : {});
  if (!comps[netMetric]) netMetric = Object.keys(comps)[0] || "pm25";
  drawNetworkChart(comps[netMetric]);
}

// pollutant selector for the cross-area comparison chart
function buildNetMetricToggle() {
  const box = el("netMetricToggle");
  if (!box || !NET) return;
  const comps = NET.comparisons || (NET.comparison ? { pm25: NET.comparison } : {});
  const keys = Object.keys(comps);
  if (keys.length <= 1) { box.innerHTML = ""; return; }  // nothing to switch between
  box.innerHTML = keys.map((k) =>
    `<button data-metric="${k}" class="${k === netMetric ? "active" : ""}">${comps[k].label || k}</button>`
  ).join("");
  box.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      box.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      netMetric = btn.dataset.metric;
      drawNetworkChart(comps[netMetric]);
    });
  });
}

function drawNetworkMap(nodes) {
  if (typeof L === "undefined" || !el("map")) return;
  if (mapObj) { mapObj.remove(); mapObj = null; }
  const pts = nodes.filter((n) => n.lat != null && n.lon != null);
  if (!pts.length) return;
  mapObj = L.map("map", { scrollWheelZoom: false, attributionControl: false });
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
  }).addTo(mapObj);
  const latlngs = [];
  pts.forEach((n) => {
    latlngs.push([n.lat, n.lon]);
    L.circleMarker([n.lat, n.lon], {
      radius: n.primary ? 11 : 8, color: n.category.color, weight: 2,
      fillColor: n.category.color, fillOpacity: 0.55,
    }).addTo(mapObj).bindPopup(
      `<b>${n.area}</b><br>${n.name || ""}<br>AQI ${n.aqi ?? "—"} · ${n.category.label}` +
      `${n.dominant ? ` (${n.dominant})` : ""}<br>${popupReadings(n.readings)}` +
      `<br><small>${n.latest_time}</small>`);
  });
  mapObj.fitBounds(latlngs, { padding: [30, 30] });
}

// unit shown on the comparison chart's y-axis for the selected pollutant
function unitForMetric(key) {
  for (const n of (NET && NET.nodes) || []) {
    const r = (n.readings || []).find((x) => x.key === key);
    if (r && r.unit) return r.unit;
  }
  return "µg/m³";
}

function drawNetworkChart(cmp) {
  if (!cmp || !el("networkChart")) return;
  if (netChart) netChart.destroy();
  // new schema: series[].values ; tolerate the old series[].pm25 too
  const seriesData = (s) => (s.values !== undefined ? s.values : s.pm25);
  setText("netChartTag", `30-day daily-mean ${cmp.label || "PM2.5"}`);
  netChart = new Chart(el("networkChart"), {
    type: "line",
    data: {
      labels: cmp.labels,
      datasets: (cmp.series || []).map((s) => ({
        label: s.area, data: seriesData(s), borderColor: s.color,
        backgroundColor: s.color, spanGaps: true,
      })),
    },
    options: {
      responsive: true, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 12, boxHeight: 12, usePointStyle: true } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
        y: { grid: { color: "#1c232c" }, title: { display: true, text: unitForMetric(netMetric) } },
      },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.5, tension: 0.25 } },
    },
  });
}

// ====================================================================
// AQI SCALE LEGEND
// ====================================================================
function renderScale(categories, aqi) {
  const box = el("scale");
  if (!categories) { box.innerHTML = ""; return; }
  box.innerHTML = categories.map((c) => {
    const active = aqi != null && aqi >= c.lo && aqi <= c.hi;
    return `<div class="seg${active ? " active" : ""}" style="--seg:${c.color}">
      <span class="seg-range">${c.lo}–${c.hi}</span>
      <span class="seg-label">${c.label}</span>
    </div>`;
  }).join("");
}

// ====================================================================
// MODEL CARD / PROVENANCE
// ====================================================================
function renderMeta(card) {
  const prov = el("prov");
  if (!card) { prov.innerHTML = `<div class="prov-item"><dd>No model card found. Run gen_model_card.py.</dd></div>`; return; }

  const st = card.station || {};
  const co = card.coverage || {};
  const sp = card.split || {};
  const fc = card.forecast || {};
  let loc = "—";
  if (st.coordinates && st.coordinates.lat != null) {
    loc = `${st.coordinates.lat.toFixed(3)}, ${st.coordinates.lon.toFixed(3)}`;
    if (st.distance_km_from_dhaka_centre != null) loc += ` · ${st.distance_km_from_dhaka_centre} km from city centre`;
  }

  setText("stationSub", st.name ? `Monitoring station · ${st.name}` : "Monitoring & Machine-Learning Prediction");
  setText("stationName", st.name || "—");

  const rows = [
    ["Live data feed", card.data_source],
    ["Monitoring station", st.name],
    ["Network / provider", st.provider ? `${st.provider} · public WiFi air-quality station (via OpenAQ)` : null],
    ["Location", loc],
    ["Pollutant", `${card.parameter} (${card.parameter_units}), ${card.cadence}`],
    ["Data coverage", `${co.start} → ${co.end}`],
    ["Total observations", co.total_hours != null ? `${co.total_hours.toLocaleString()} hourly records` : null],
    ["Training window", `${sp.train_start} → ${sp.train_end} · ${sp.train_hours?.toLocaleString()} h`],
    ["Validation window", `${sp.test_start} → ${sp.test_end} · ${sp.test_hours?.toLocaleString()} h`],
    ["Split", sp.scheme],
    ["Model", `${card.model}${card.model_note ? " — " + card.model_note : ""}`],
    ["Predicts", card.target],
    ["Forecasting", `${fc.method} · up to ${fc.max_hours} h ahead`],
    ["Trained", card.trained_at],
  ];
  prov.innerHTML = rows
    .filter(([, v]) => v != null && v !== "undefined" && !String(v).includes("undefined"))
    .map(([k, v]) => `<div class="prov-item"><dt>${k}</dt><dd>${v}</dd></div>`)
    .join("");

  // feature chips
  setText("featCount", card.n_features);
  el("featChips").innerHTML = (card.features || [])
    .map((f) => `<span class="chip" title="${f.name}">${f.desc}</span>`).join("");
}

// ====================================================================
// QUICK STAT TILES
// ====================================================================
function renderStats(card) {
  const box = el("stats");
  if (!card) { box.innerHTML = ""; return; }
  const co = card.coverage || {}, sp = card.split || {}, fc = card.forecast || {}, m = card.metrics || {};
  const tiles = [
    ["Observations", co.total_hours != null ? co.total_hours.toLocaleString() : "—", "hours of data"],
    ["Trained on", sp.train_hours != null ? sp.train_hours.toLocaleString() : "—", "hours"],
    ["Validated on", sp.test_hours != null ? sp.test_hours.toLocaleString() : "—", "unseen hours"],
    ["Forecast", `≤ ${fc.max_hours ?? "—"}`, "hours ahead"],
    ["Fit (R²)", m.pm25_r2 ?? "—", "of variance explained"],
  ];
  box.innerHTML = tiles.map(([k, v, h]) =>
    `<div class="stat"><span class="stat-num">${v}</span><span class="stat-key">${k}</span><span class="stat-hint">${h}</span></div>`
  ).join("");
}

// ====================================================================
// PERFORMANCE
// ====================================================================
function renderMetrics(card, summaryMetrics) {
  const m = (card && card.metrics) || summaryMetrics || {};
  const sp = (card && card.split) || {};
  const tiles = [
    ["Best model", m.best_model ?? "—", "", "chosen by lowest RMSE"],
    ["PM2.5 MAE", fmt(m.pm25_mae), "µg/m³", "average miss per hour"],
    ["PM2.5 RMSE", fmt(m.pm25_rmse), "µg/m³", "typical miss (penalises big errors)"],
    ["R²", fmt(m.pm25_r2), "", "1.0 = perfect"],
    ["AQI MAE", fmt(m.aqi_mae), "pts", "avg AQI-point error"],
  ];
  el("metrics").innerHTML = tiles.map(([k, v, u, h]) =>
    `<div class="metric"><span class="label">${k}</span>` +
    `<span class="num">${v}${u ? `<span class="hint">${u}</span>` : ""}</span>` +
    `<span class="metric-desc">${h}</span></div>`
  ).join("");

  if (m.pm25_r2 != null) {
    setText("perfInterpret",
      `On ${sp.test_hours ? sp.test_hours.toLocaleString() + " " : ""}hours it never saw during training, the model's PM2.5 estimate lands within about ${m.pm25_mae} µg/m³ of the real value on average, explaining ${Math.round(m.pm25_r2 * 100)}% of the hour-to-hour variation.`);
  }
  if (sp.test_start) {
    setText("validNote",
      `Each point is one of ${sp.test_hours?.toLocaleString()} hours from ${sp.test_start} to ${sp.test_end} that the model never saw while training. The closer the two lines, the better the prediction.`);
  }
}

// ====================================================================
// WHAT IT PREDICTS
// ====================================================================
function renderExplainer(card) {
  const m = card?.model || "the model";
  setText("predictExplain",
    `Readings come from public air-quality monitoring stations across Dhaka, streamed in through the OpenAQ v3 API; the model is trained on the primary station in Uttara. ` +
    `This model is a short-horizon nowcaster. Each hour it estimates the next hour's PM2.5 from the recent past — the last few hourly readings, the 6- and 24-hour rolling averages, and the time of day, day of week and month. That predicted PM2.5 is then converted into a US AQI value and category using the EPA 2024 breakpoints. ` +
    `The forecast below extends this recursively: each predicted hour is fed back in as the input for the next, so accuracy is strongest in the first few hours and softens further out. Because recent PM2.5 dominates the inputs, ${m} behaves like a smart persistence model — strong for the next few hours, not a substitute for a weather-driven multi-day forecast. ` +
    `You can point the forecast at any station in the city with the Area selector — the same model runs on that station's own recent readings.`);
}

// ====================================================================
// CHARTS
// ====================================================================
function lineChart(ctx, datasets, labels, yTitle = "µg/m³") {
  return new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 12, boxHeight: 12 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
        y: { grid: { color: "#1c232c" }, title: { display: true, text: yTitle } },
      },
      elements: { point: { radius: 0 }, line: { borderWidth: 1.6, tension: 0.25 } },
    },
  });
}

let historyChart, predChart, forecastChart;

// build the channel selector buttons from the live channel list
function buildMetricToggle() {
  const box = el("metricToggle");
  box.innerHTML = CHANNELS.map((ch, i) =>
    `<button data-metric="${ch.key}" class="${ch.key === curMetric ? "active" : ""}">${ch.label}</button>`
  ).join("");
  box.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      box.querySelectorAll("button").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      curMetric = btn.dataset.metric;
      loadTrend();
    });
  });
}

function hexToRgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

async function loadTrend() {
  const h = await getJSON(`/api/history?days=${curDays}`);
  if (h.error || !h[curMetric]) return;
  const ch = CHANNELS.find((c) => c.key === curMetric) || { label: curMetric, unit: "", color: ACCENT };
  const ctx = el("historyChart");
  if (historyChart) historyChart.destroy();
  historyChart = lineChart(ctx, [{
    label: ch.label, data: h[curMetric], borderColor: ch.color,
    backgroundColor: hexToRgba(ch.color, 0.12), fill: true,
  }], h.labels, ch.unit);
  setText("trendNote",
    `Measured hourly ${ch.label} (${ch.unit}) recorded at this station — the real, observed past.`);
}

async function loadPredictions() {
  const p = await getJSON("/api/predictions");
  if (p.error) return;
  const ctx = el("predChart");
  if (predChart) predChart.destroy();
  predChart = lineChart(ctx, [
    { label: "Actual", data: p.actual, borderColor: "#7aa2c4" },
    { label: "Predicted", data: p.predicted, borderColor: WARN, borderDash: [4, 3] },
  ], p.labels);
}

// populate the forecast area picker from the live network nodes (primary first)
function buildForecastAreas(net) {
  const sel = el("forecastArea");
  if (!sel || !net || !net.nodes) return;
  const nodes = [...net.nodes].sort((a, b) => (b.primary ? 1 : 0) - (a.primary ? 1 : 0));
  sel.innerHTML = nodes.map((n) =>
    `<option value="${n.area}">${n.area}${n.primary ? " (primary)" : ""}</option>`).join("");
  const primary = nodes.find((n) => n.primary) || nodes[0];
  curArea = primary ? primary.area : "";
  sel.value = curArea;
  sel.onchange = () => { curArea = sel.value; loadForecast(); };
}

async function loadForecast() {
  const f = await getJSON(`/api/forecast?hours=${curFcHours}&area=${encodeURIComponent(curArea)}`);
  const note = el("forecastNote");
  if (f.error) { note.textContent = f.error; if (forecastChart) forecastChart.destroy(); return; }
  const peak = Math.max(...f.pm25), peakAt = f.labels[f.pm25.indexOf(peak)];
  const where = f.area || "the primary node";
  note.textContent = `Recursive projection for ${where} from its latest reading${f.from ? ` (${f.from})` : ""}. Peak in this window: ${peak} µg/m³ around ${peakAt}.`;
  const ctx = el("forecastChart");
  if (forecastChart) forecastChart.destroy();
  forecastChart = lineChart(ctx, [{
    label: `Forecast PM2.5 — ${where}`, data: f.pm25, borderColor: "#c98bdb",
    backgroundColor: "rgba(201,139,219,0.12)", fill: true,
  }], f.labels);
}

// ---- toggles ----
function wireToggle(id, attr, handler) {
  document.querySelectorAll(`#${id} button`).forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(`#${id} button`).forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      handler(parseInt(btn.dataset[attr], 10));
    });
  });
}

// ---- init ----
async function init() {
  const [s, meta, net] = await Promise.all([
    getJSON("/api/summary"), getJSON("/api/meta"), getJSON("/api/network")]);
  const card = meta && meta.card;
  renderSummary(s);
  renderNetwork(net);
  renderLive(s && s.channels, s && s.now, s && s.latest_time);
  renderScale(meta && meta.categories, s && s.aqi);
  renderMeta(card);
  renderStats(card);
  renderMetrics(card, s && s.metrics);
  renderExplainer(card);
  buildMetricToggle();
  buildForecastAreas(net);
  loadTrend();
  loadPredictions();
  loadForecast();
  wireToggle("rangeToggle", "days", (d) => { curDays = d; loadTrend(); });
  wireToggle("forecastToggle", "hours", (h) => { curFcHours = h; loadForecast(); });
}
init();
