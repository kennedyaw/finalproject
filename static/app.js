// Near-Earth Asteroid Tracker: front end.
// Screen 1 (list) and Screen 2 (detail) live on one page; the URL hash decides
// which one shows: "" = list, "#/asteroid/3542519" = detail.

const els = {
  weekLabel: document.getElementById("week-label"),
  toggle: document.getElementById("hazard-toggle"),
  banner: document.getElementById("banner"),
  listView: document.getElementById("list-view"),
  detailView: document.getElementById("detail-view"),
  cards: document.getElementById("cards"),
  count: document.getElementById("count"),
  updated: document.getElementById("updated"),
};

// Lets you demo a different week: http://localhost:5000/?today=2026-11-02
const pageParams = new URLSearchParams(location.search);
const todayOverride = pageParams.get("today");

function apiUrl(path, params = {}) {
  const q = new URLSearchParams(params);
  if (todayOverride) q.set("today", todayOverride);
  const qs = q.toString();
  return qs ? `${path}?${qs}` : path;
}

async function getJson(url) {
  const resp = await fetch(url);
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(body.error || body?.meta?.warning || `Request failed (${resp.status})`);
    err.body = body;
    throw err;
  }
  return body;
}

// ---------- formatting helpers ----------
const fmt = (n, digits = 0) =>
  n == null ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: digits });

function sizeText(a) {
  return `${fmt(a.estimated_diameter_min_m)}–${fmt(a.estimated_diameter_max_m)} m`;
}

function prettyDate(iso) {
  // "2026-11-11" -> "Wed, Nov 11" (parsed as a calendar date, not a UTC time)
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showMeta(meta) {
  if (!meta) return;
  els.weekLabel.textContent = `Close approaches for ${prettyDate(meta.week_start)} – ${prettyDate(meta.week_end)}`;
  els.updated.textContent = meta.last_updated
    ? `Last updated ${new Date(meta.last_updated).toLocaleString()}.`
    : "";
  if (meta.warning) {
    els.banner.hidden = false;
    els.banner.textContent = meta.last_updated
      ? `Couldn't refresh from NASA right now, so you're seeing saved data. (${meta.warning})`
      : `Couldn't load data from NASA. ${meta.warning}`;
  } else {
    els.banner.hidden = true;
  }
}

// ---------- Screen 1: list ----------
async function loadList() {
  els.cards.innerHTML = `<p class="empty">Loading asteroids…</p>`;
  const params = els.toggle.checked ? { hazardous: "true" } : {};
  try {
    const data = await getJson(apiUrl("/asteroids", params));
    showMeta(data.meta);
    renderCards(data.asteroids);
  } catch (err) {
    showMeta(err.body?.meta);
    els.cards.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
    els.count.textContent = "";
  }
}

function renderCards(asteroids) {
  const filtered = els.toggle.checked;
  els.count.textContent = `${asteroids.length} ${filtered ? "potentially hazardous " : ""}close approach${asteroids.length === 1 ? "" : "es"} this week`;
  if (!asteroids.length) {
    els.cards.innerHTML = `<p class="empty">${filtered ? "No potentially hazardous asteroids this week. 🎉" : "No close approaches found for this week."}</p>`;
    return;
  }
  let html = "";
  let lastDate = null;
  for (const a of asteroids) {
    if (a.close_approach_date !== lastDate) {
      html += `<h3 class="date-heading">${prettyDate(a.close_approach_date)}</h3>`;
      lastDate = a.close_approach_date;
    }
    html += `
      <article class="card ${a.is_potentially_hazardous ? "hazardous" : ""}">
        ${a.is_potentially_hazardous ? `<span class="badge">Potentially hazardous</span>` : ""}
        <h3>${escapeHtml(a.name)}</h3>
        <div class="row"><span>Date</span><span>${prettyDate(a.close_approach_date)}</span></div>
        <div class="row"><span>Miss distance</span><span>${fmt(a.miss_distance_miles)} mi</span></div>
        <div class="row"><span>Est. size</span><span>${sizeText(a)}</span></div>
        <button type="button" data-id="${escapeHtml(a.id)}">View details</button>
      </article>`;
  }
  els.cards.innerHTML = html;
}

els.cards.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-id]");
  if (btn) location.hash = `#/asteroid/${btn.dataset.id}`;
});
els.toggle.addEventListener("change", loadList);

// ---------- Feature 3: size chart ----------
async function drawChart(targetId, highlightId) {
  const target = document.getElementById(targetId);
  if (typeof Plotly === "undefined") {
    target.innerHTML = `<p class="empty">Chart library didn't load (are you offline?).</p>`;
    return;
  }
  try {
    const data = await getJson(apiUrl("/asteroids/chart-data", highlightId ? { include: highlightId } : {}));
    const rocks = data.asteroids;
    const marks = data.landmarks;
    if (!rocks.length) {
      target.innerHTML = `<p class="empty">No asteroids to chart this week.</p>`;
      return;
    }
    const colorFor = (r) =>
      r.id === highlightId ? "#7fb5e6" : r.hazardous ? "#e8836a" : "#b9a7e8";

    const asteroidTrace = {
      type: "bar",
      name: "Asteroids (diameter)",
      x: rocks.map((r) => r.name),
      y: rocks.map((r) => r.avg_m),
      marker: { color: rocks.map(colorFor) },
      error_y: {
        type: "data", symmetric: false, color: "#2f2b45", thickness: 1, width: 5,
        array: rocks.map((r) => r.max_m - r.avg_m),
        arrayminus: rocks.map((r) => r.avg_m - r.min_m),
      },
      hovertemplate: "%{x}<br>~%{y:,.0f} m across<extra></extra>",
    };
    const landmarkTrace = {
      type: "bar",
      name: "Landmarks (height)",
      x: marks.map((m) => m.name),
      y: marks.map((m) => m.meters),
      marker: { color: "#cfe3c8" },
      hovertemplate: "%{x}<br>%{y:,.0f} m tall<extra></extra>",
    };
    // Some asteroids are kilometers wide; switch to a log scale so landmarks stay visible.
    const biggest = Math.max(...rocks.map((r) => r.max_m), ...marks.map((m) => m.meters));
    const useLog = biggest > 3000;

    Plotly.newPlot(target, [asteroidTrace, landmarkTrace], {
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: '"Times New Roman", Times, serif', color: "#2f2b45", size: 15 },
      hoverlabel: { font: { family: '"Times New Roman", Times, serif' } },
      margin: { t: 30, r: 10, b: 130, l: 90 },
      yaxis: { title: { text: useLog ? "meters (log scale)" : "meters" }, type: useLog ? "log" : "linear", gridcolor: "#efeaf6", zeroline: false },
      xaxis: { tickangle: -30, automargin: true },
      legend: { orientation: "h", x: 0, xanchor: "left", y: 1.14, entrywidth: 200, font: { size: 14 } },
      bargap: 0.35,
      barmode: "group",
    }, { responsive: true, displayModeBar: false });
  } catch (err) {
    target.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
  }
}

// ---------- Screen 2: detail ----------
async function loadDetail(id) {
  const name = document.getElementById("detail-name");
  const badge = document.getElementById("detail-badge");
  const stats = document.getElementById("detail-stats");
  const link = document.getElementById("detail-link");
  name.textContent = "Loading…";
  badge.innerHTML = stats.innerHTML = link.innerHTML = "";
  try {
    const a = await getJson(apiUrl(`/asteroids/${encodeURIComponent(id)}`));
    name.textContent = a.name;
    badge.innerHTML = a.is_potentially_hazardous
      ? `<span class="badge">Potentially hazardous</span>`
      : `<span class="subtle">Not classified as potentially hazardous</span>`;
    const rows = [
      ["Closest approach", prettyDate(a.close_approach_date)],
      ["Miss distance", `${fmt(a.miss_distance_miles)} mi (${fmt(a.miss_distance_lunar, 1)}× Moon distance)`],
      ["Velocity", `${fmt(a.relative_velocity_mph)} mph`],
      ["Est. diameter", sizeText(a)],
      ["Magnitude (H)", fmt(a.absolute_magnitude_h, 2)],
      ["Orbit class", a.orbit_class ? `${a.orbit_class}${a.orbit_class_description ? ` — ${a.orbit_class_description}` : ""}` : "Unavailable right now"],
    ];
    stats.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("");
    if (a.nasa_jpl_url) {
      link.innerHTML = `<a href="${escapeHtml(a.nasa_jpl_url)}" target="_blank" rel="noopener">See it in NASA JPL's database ↗</a>`;
    }
    drawChart("detail-chart", a.id);
  } catch (err) {
    name.textContent = "Couldn't load this asteroid";
    stats.innerHTML = `<dd>${escapeHtml(err.message)}</dd>`;
  }
}

// ---------- routing between the two screens ----------
function route() {
  const match = location.hash.match(/^#\/asteroid\/(\d+)$/);
  if (match) {
    els.listView.hidden = true;
    els.detailView.hidden = false;
    window.scrollTo(0, 0);
    loadDetail(match[1]);
  } else {
    els.detailView.hidden = true;
    els.listView.hidden = false;
    // A chart drawn while hidden has no width; resize it now that it's visible.
    const overview = document.getElementById("overview-chart");
    if (typeof Plotly !== "undefined" && overview.data) Plotly.Plots.resize(overview);
  }
}

document.querySelector(".back").addEventListener("click", (e) => {
  e.preventDefault();
  location.hash = "";
});
window.addEventListener("hashchange", route);

loadList();
drawChart("overview-chart");
route();
