// Stormbook demo dashboard — static, read-only.
// Pulls mock JSON, renders leads table, KPI cards, funnel chart, weekly calendar.

const DATA_URL = "data/mock.json";

const DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_LABELS = { Mon: "Mon", Tue: "Tue", Wed: "Wed", Thu: "Thu", Fri: "Fri", Sat: "Sat", Sun: "Sun" };

const CAL_START_HOUR = 8;
const CAL_END_HOUR = 18;
const SLOT_HEIGHT_PX = 38;
const BLOCK_DURATION_MIN = 60; // each inspection visually rendered as a 1-hour block

const STATUS_CLASS = {
  Awaiting: "badge--awaiting",
  Qualifying: "badge--qualifying",
  Booked: "badge--booked",
  DQ: "badge--dq",
};

document.addEventListener("DOMContentLoaded", init);

async function init() {
  let data;
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    data = await res.json();
  } catch (err) {
    console.error("Failed to load mock data:", err);
    renderError(err.message);
    return;
  }

  renderLeads(data.today_leads);
  renderKpis(data.metrics);
  renderCalendar(data.inspections_this_week);

  // Chart.js loads via `defer`; ensure it's ready before drawing.
  whenChartReady(() => renderFunnel(data.funnel_30d));

  wireClientSelector();
}

/* ---------- Leads table ---------- */
function renderLeads(leads) {
  const tbody = document.getElementById("leads-body");
  const meta = document.getElementById("leads-meta");
  if (!tbody) return;

  tbody.innerHTML = "";

  for (const lead of leads) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(lead.name)}</td>
      <td class="col-source">${escapeHtml(lead.source)}</td>
      <td class="col-time">${formatLeadTime(lead.received_at)}</td>
      <td><span class="badge ${STATUS_CLASS[lead.status] || ""}">${escapeHtml(lead.status)}</span></td>
    `;
    tbody.appendChild(tr);
  }

  if (meta) meta.textContent = `${leads.length} today`;
}

// Read HH:MM straight from an ISO string so the displayed time matches the
// mock data regardless of the viewer's local timezone.
function formatLeadTime(iso) {
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  let hh = parseInt(m[1], 10);
  const mm = m[2];
  const period = hh >= 12 ? "PM" : "AM";
  const display = ((hh + 11) % 12) + 1;
  return `${display}:${mm} ${period}`;
}

/* ---------- KPI cards ---------- */
function renderKpis(m) {
  setKpi("kpi-first-response", formatSecondsShort(m.avg_first_response_seconds),
    m.avg_first_response_seconds < 60);
  setKpi("kpi-time-to-book", formatMinSec(m.avg_time_to_book_seconds),
    m.avg_time_to_book_seconds < 240);
  setKpi("kpi-held-rate", formatPct(m.booked_to_held_rate),
    m.booked_to_held_rate > 0.75);
  setKpi("kpi-inspections-7d", String(m.inspections_booked_7d), null);
}

function setKpi(id, value, ok) {
  const card = document.getElementById(id);
  if (!card) return;
  card.querySelector('[data-field="value"]').textContent = value;
  const statusEl = card.querySelector('[data-field="status"]');
  if (!statusEl) return;
  if (ok === null) {
    statusEl.textContent = "";
    statusEl.className = "kpi__status";
    return;
  }
  if (ok) {
    statusEl.textContent = "On target";
    statusEl.className = "kpi__status kpi__status--ok";
  } else {
    statusEl.textContent = "Off target";
    statusEl.className = "kpi__status kpi__status--warn";
  }
}

function formatSecondsShort(s) { return `${s}s`; }
function formatMinSec(s) {
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${String(r).padStart(2, "0")}s`;
}
function formatPct(x) { return `${Math.round(x * 100)}%`; }

/* ---------- Funnel chart ---------- */
function renderFunnel(funnel) {
  const canvas = document.getElementById("funnel-chart");
  if (!canvas || typeof Chart === "undefined") return;

  const stages = [
    { label: "Leads",     value: funnel.leads },
    { label: "Qualified", value: funnel.qualified },
    { label: "Booked",    value: funnel.booked },
    { label: "Held",      value: funnel.held },
    { label: "Won",       value: funnel.won },
  ];
  const leads = funnel.leads || 1;
  const labels = stages.map((s) => `${s.label}  ${s.value}  ·  ${Math.round((s.value / leads) * 100)}%`);
  const values = stages.map((s) => s.value);

  // Yellow → amber gradient across stages (visually narrowing funnel).
  const colors = ["#FFD500", "#FFC233", "#FFB347", "#FFA060", "#FF8C42"];

  new Chart(canvas, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 6,
        borderSkipped: false,
        barPercentage: 0.85,
        categoryPercentage: 0.85,
      }],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 600 },
      layout: { padding: { right: 20 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.x;
              return `${v} (${Math.round((v / leads) * 100)}% of leads)`;
            },
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { color: "#8A8A91", font: { family: "JetBrains Mono", size: 11 } },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: {
          ticks: {
            color: "#F5F5F7",
            font: { family: "Inter Tight", size: 13, weight: "600" },
            padding: 6,
          },
          grid: { display: false },
        },
      },
    },
  });
}

/* ---------- Weekly calendar ---------- */
function renderCalendar(items) {
  const cal = document.getElementById("calendar");
  const meta = document.getElementById("cal-meta");
  if (!cal) return;

  cal.innerHTML = "";

  // Time column
  const timeCol = document.createElement("div");
  timeCol.className = "cal-col cal-col--times";
  timeCol.appendChild(elem("div", "cal-head", ""));
  const timesWrap = document.createElement("div");
  timesWrap.className = "cal-times";
  for (let h = CAL_START_HOUR; h < CAL_END_HOUR; h++) {
    timesWrap.appendChild(elem("div", "cal-time-slot", formatHourLabel(h)));
  }
  timeCol.appendChild(timesWrap);
  cal.appendChild(timeCol);

  // Day columns
  const byDay = groupByDay(items);
  for (const day of DAY_ORDER) {
    const col = document.createElement("div");
    col.className = "cal-col cal-col--day";
    col.appendChild(elem("div", "cal-head", DAY_LABELS[day]));

    const body = document.createElement("div");
    body.className = "cal-col__body";

    for (const it of byDay[day] || []) {
      body.appendChild(renderInspectionBlock(it));
    }
    col.appendChild(body);
    cal.appendChild(col);
  }

  if (meta) meta.textContent = `${items.length} inspections booked`;
}

function groupByDay(items) {
  const out = {};
  for (const day of DAY_ORDER) out[day] = [];
  for (const it of items) {
    if (out[it.day]) out[it.day].push(it);
  }
  return out;
}

function renderInspectionBlock(it) {
  const [hh, mm] = it.time.split(":").map(Number);
  const minutesFromStart = (hh - CAL_START_HOUR) * 60 + mm;
  const top = (minutesFromStart / 60) * SLOT_HEIGHT_PX;
  const height = (BLOCK_DURATION_MIN / 60) * SLOT_HEIGHT_PX - 4;

  const block = document.createElement("div");
  block.className = "cal-block";
  block.style.top = `${top}px`;
  block.style.height = `${height}px`;
  block.innerHTML = `
    <span class="cal-block__time">${escapeHtml(formatTime12h(it.time))}</span>
    <span class="cal-block__name">${escapeHtml(it.name)}</span>
    <span class="cal-block__tag">${escapeHtml(it.damage)}</span>
  `;
  return block;
}

function formatHourLabel(h) {
  if (h === 12) return "12 PM";
  if (h === 0) return "12 AM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}

function formatTime12h(t) {
  const [hStr, m] = t.split(":");
  let h = parseInt(hStr, 10);
  const period = h >= 12 ? "PM" : "AM";
  h = ((h + 11) % 12) + 1;
  return `${h}:${m} ${period}`;
}

/* ---------- Client selector (cosmetic) ---------- */
function wireClientSelector() {
  const sel = document.getElementById("client-select");
  if (!sel) return;
  sel.addEventListener("change", () => {
    // v1: cosmetic only — no dataset swap. Re-render leaves data identical.
    sel.blur();
  });
}

/* ---------- Utilities ---------- */
function elem(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function whenChartReady(cb) {
  if (typeof Chart !== "undefined") { cb(); return; }
  const t = setInterval(() => {
    if (typeof Chart !== "undefined") {
      clearInterval(t);
      cb();
    }
  }, 30);
  setTimeout(() => clearInterval(t), 5000);
}

function renderError(msg) {
  const main = document.querySelector(".dashboard");
  if (!main) return;
  const banner = document.createElement("div");
  banner.className = "card";
  banner.innerHTML = `<strong>Could not load mock data.</strong><br/><span style="color:var(--muted)">${escapeHtml(msg)}</span>`;
  main.prepend(banner);
}
