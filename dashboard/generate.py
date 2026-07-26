"""Builds a static, self-contained, searchable/sortable HTML dashboard from
the current data/*.jsonl files, plus a daily-volume chart per candidate.
No server, no network requests -- everything (data + charting) is inlined,
so it works as a plain static file (used for both local viewing and
GitHub Pages, which serves it from docs/index.html).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "candidates.yaml"
OUTPUT_PATH = PROJECT_ROOT / "docs" / "index.html"

# Categorical palette, fixed order (dataviz skill reference palette) -- 3
# candidates fits comfortably in slots 1-3 (blue/green/magenta).
SERIES_COLORS = [
    {"light": "#2a78d6", "dark": "#3987e5"},
    {"light": "#008300", "dark": "#008300"},
    {"light": "#e87ba4", "dark": "#d55181"},
]


def _load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _load_candidates_full() -> list:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["candidates"]


def load_records() -> list:
    candidates = {c["id"]: c for c in _load_candidates_full()}
    records = []

    for path in sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl")):
        for item in _load_jsonl(path):
            info = candidates.get(item.get("candidate_id"), {})
            records.append(
                {
                    "status": "accepted",
                    "candidate_id": item.get("candidate_id", ""),
                    "candidate_name": info.get("name", item.get("candidate_id", "")),
                    "office": info.get("office", ""),
                    "title": item.get("title", ""),
                    "source": item.get("source", ""),
                    "collector": item.get("collector", ""),
                    "published_at": item.get("published_at", ""),
                    "url": item.get("source_url", ""),
                    "reason": "",
                    "topic": item.get("topic", ""),
                    "stance": item.get("stance_toward_candidate", ""),
                    "risk_level": item.get("risk_level", ""),
                    "cluster_size": item.get("cluster_size", 1),
                    "classified_by": item.get("classified_by", ""),
                    "matched_on": item.get("matched_alias", ""),
                    "matched_context": item.get("matched_require_any", ""),
                    "backfilled": bool(item.get("backfilled", False)),
                    "summary": item.get("summary", ""),
                }
            )

    for path in sorted(DATA_DIR.glob("rejections-*.jsonl")):
        for item in _load_jsonl(path):
            info = candidates.get(item.get("candidate_id"), {})
            records.append(
                {
                    "status": "rejected",
                    "candidate_id": item.get("candidate_id", ""),
                    "candidate_name": info.get("name", item.get("candidate_name", "")),
                    "office": info.get("office", ""),
                    "title": item.get("title", ""),
                    "source": "",
                    "collector": item.get("collector", ""),
                    "published_at": "",
                    "url": item.get("source_url", ""),
                    "reason": item.get("reason", ""),
                    "topic": "",
                    "stance": "",
                    "risk_level": "",
                    "cluster_size": 1,
                    "classified_by": "",
                    "matched_on": "",
                    "matched_context": "",
                    "backfilled": False,
                    "summary": "",
                }
            )

    return records


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Candidate Research Monitor</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #fcfcfb; --page: #f9f9f7; --fg: #0b0b0b; --muted: #898781; --secondary: #52514e;
    --border: #e1e0d9; --axis: #c3c2b7;
    --accept: #0ca30c; --accept-bg: #e3f7e3;
    --reject: #d03b3b; --reject-bg: #fbe7e6;
    --row-hover: #f3f3f1; --input-bg: #ffffff; --card-bg: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a19; --page: #0d0d0d; --fg: #ffffff; --muted: #898781; --secondary: #c3c2b7;
      --border: #2c2c2a; --axis: #383835;
      --accept: #0ca30c; --accept-bg: #123312;
      --reject: #e66767; --reject-bg: #3a1414;
      --row-hover: #232322; --input-bg: #1a1a19; --card-bg: #1a1a19;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 1.5rem; background: var(--page); color: var(--fg);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px;
  }
  h1 { font-size: 1.25rem; margin: 0 0 0.25rem; }
  h2 { font-size: 0.95rem; margin: 0 0 0.75rem; color: var(--fg); }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 1.25rem; }
  .card {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 1rem 1.25rem; margin-bottom: 1.25rem;
  }
  .summary { display: flex; gap: 1.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .stat { font-size: 0.85rem; color: var(--muted); }
  .stat b { color: var(--fg); font-size: 1.1rem; font-weight: 600; }
  .names-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.75rem; }
  .name-card { border: 1px solid var(--border); border-radius: 8px; padding: 0.6rem 0.8rem; }
  .name-card .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 0.4rem; }
  .name-card .cname { font-weight: 600; }
  .name-card .office { color: var(--muted); font-size: 0.78rem; margin-top: 0.15rem; }
  .name-card .aliases { color: var(--secondary); font-size: 0.78rem; margin-top: 0.35rem; }
  .name-card .aliases b { color: var(--fg); font-weight: 600; }
  .controls { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  input[type=text], select {
    padding: 0.45rem 0.6rem; border: 1px solid var(--border); border-radius: 6px;
    background: var(--input-bg); color: var(--fg); font-size: 0.85rem;
  }
  input[type=text] { flex: 1; min-width: 200px; }
  select { cursor: pointer; }
  a { color: inherit; }
  .empty { color: var(--muted); padding: 2rem; text-align: center; }
  .muted-dash { color: var(--muted); }

  .tabs { display: flex; gap: 0.25rem; margin-bottom: 1rem; border-bottom: 1px solid var(--border); }
  .tab {
    padding: 0.6rem 1rem; font-size: 0.85rem; font-weight: 600; color: var(--muted);
    cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px; user-select: none;
  }
  .tab:hover { color: var(--fg); }
  .tab.active { color: var(--fg); border-bottom-color: var(--fg); }
  .tab .count { color: var(--muted); font-weight: 500; margin-left: 0.3rem; }
  .tab.active .count { color: var(--secondary); }
  .view { display: none; }
  .view.active { display: block; }

  /* Findings feed (accepted items) */
  .story-card {
    background: var(--card-bg); border: 1px solid var(--border); border-left: 3px solid var(--candidate-color, var(--border));
    border-radius: 8px; padding: 0.9rem 1.1rem; margin-bottom: 0.75rem;
  }
  .story-card-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; flex-wrap: wrap; }
  .candidate-name { font-weight: 600; font-size: 0.78rem; color: var(--candidate-color, var(--fg)); }
  .story-date { color: var(--muted); font-size: 0.76rem; }
  .story-title {
    display: block; font-weight: 600; font-size: 0.95rem; color: var(--fg); text-decoration: none;
    line-height: 1.35; margin-bottom: 0.3rem;
  }
  .story-title:hover { text-decoration: underline; }
  .story-title.no-link { color: var(--fg); }
  .story-summary { color: var(--secondary); font-size: 0.85rem; margin-bottom: 0.55rem; line-height: 1.45; }
  .story-footer { display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }
  .story-source { color: var(--muted); font-size: 0.76rem; margin-left: auto; }
  .badge {
    display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
    font-size: 0.68rem; font-weight: 600; white-space: nowrap;
  }
  .badge.backfilled { color: var(--secondary); background: var(--border); font-weight: 500; }
  .mini-badge {
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 5px;
    font-size: 0.72rem; background: var(--border); color: var(--secondary);
  }
  .mini-badge.risk-elevated, .mini-badge.risk-high { background: var(--reject-bg); color: var(--reject); }
  .mini-badge.stance-favorable { background: var(--accept-bg); color: var(--accept); }
  .mini-badge.stance-unfavorable { background: var(--reject-bg); color: var(--reject); }

  /* Audit log (rejected items) -- compact table, this is a debug/audit view not the primary read */
  .table-card { padding: 0; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  col.col-candidate { width: 160px; }
  col.col-title { width: auto; }
  col.col-collector { width: 110px; }
  col.col-reason { width: 220px; }
  th, td {
    text-align: left; padding: 0.55rem 0.9rem; border-bottom: 1px solid var(--border);
    vertical-align: top; font-size: 0.82rem;
  }
  th {
    color: var(--muted); font-weight: 600; font-size: 0.7rem; text-transform: uppercase;
    letter-spacing: 0.04em; position: sticky; top: 0; background: var(--bg); white-space: nowrap;
  }
  tbody tr:hover { background: var(--row-hover); }
  .reason-text { color: var(--reject); font-size: 0.78rem; }
  .chart-wrap { position: relative; }
  .chart-legend { display: flex; gap: 1rem; margin-bottom: 0.5rem; flex-wrap: wrap; }
  .chart-legend .key { display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--secondary); }
  .chart-legend .key .line { width: 14px; height: 2px; border-radius: 1px; }
  .chart-tooltip {
    position: absolute; pointer-events: none; background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.5rem 0.65rem; font-size: 0.78rem; box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    display: none; min-width: 140px; z-index: 5;
  }
  .chart-tooltip .date { color: var(--muted); font-size: 0.72rem; margin-bottom: 0.3rem; }
  .chart-tooltip .row { display: flex; align-items: center; gap: 0.4rem; justify-content: space-between; }
  .chart-tooltip .row .line { width: 10px; height: 2px; flex-shrink: 0; }
  .chart-tooltip .row .val { font-weight: 600; margin-left: auto; }
  .chart-tooltip .row .lbl { color: var(--secondary); }
  svg text { fill: var(--muted); font-size: 10px; }
  .crosshair { stroke: var(--axis); stroke-width: 1; }
</style>
</head>
<body>
<h1>Candidate Research Monitor</h1>
<div class="subtitle">Generated __GENERATED_AT__ &middot; __BACKFILL_NOTE__</div>

<div class="card">
  <h2>Tracked names</h2>
  <div class="names-grid">
    __NAME_CARDS__
  </div>
</div>

<div class="card">
  <h2>Daily hit volume (accepted items, by publish date)</h2>
  <div class="chart-legend" id="chart-legend"></div>
  <div class="chart-wrap">
    <svg id="chart" viewBox="0 0 900 260" width="100%" height="260" preserveAspectRatio="none"></svg>
    <div class="chart-tooltip" id="chart-tooltip"></div>
  </div>
</div>

<div class="tabs">
  <div class="tab active" id="tab-findings" data-tab="findings">Findings <span class="count" id="count-findings">0</span></div>
  <div class="tab" id="tab-audit" data-tab="audit">Audit log <span class="count" id="count-audit">0</span></div>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search title, source, candidate, summary...">
  <select id="filter-candidate">
    <option value="all">All candidates</option>
  </select>
  <select id="sort-order">
    <option value="date-desc">Newest first</option>
    <option value="date-asc">Oldest first</option>
    <option value="risk">Risk level</option>
    <option value="candidate">Candidate</option>
  </select>
</div>

<div class="view active" id="view-findings">
  <div id="findings-feed"></div>
  <div class="empty" id="findings-empty" style="display:none">No findings match.</div>
</div>

<div class="view" id="view-audit">
  <div class="card table-card">
    <table>
      <colgroup>
        <col class="col-candidate"><col class="col-title"><col class="col-collector"><col class="col-reason">
      </colgroup>
      <thead>
        <tr>
          <th>Candidate</th>
          <th>Title</th>
          <th>Collector</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody id="audit-rows"></tbody>
    </table>
    <div class="empty" id="audit-empty" style="display:none">No rejections match.</div>
  </div>
</div>

<script type="application/json" id="data">__DATA_JSON__</script>
<script type="application/json" id="series-colors">__SERIES_COLORS_JSON__</script>
<script>
const records = JSON.parse(document.getElementById('data').textContent);
const seriesColors = JSON.parse(document.getElementById('series-colors').textContent);
const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;

const candidateSelect = document.getElementById('filter-candidate');
const candidateNames = [...new Set(records.map(r => r.candidate_name))].sort();
for (const name of candidateNames) {
  const opt = document.createElement('option');
  opt.value = name; opt.textContent = name;
  candidateSelect.appendChild(opt);
}

const candidateColor = {};
[...new Map(records.map(r => [r.candidate_id, r.candidate_name])).entries()]
  .sort((a, b) => a[1].localeCompare(b[1]))
  .forEach(([cid], i) => {
    candidateColor[cid] = seriesColors[i % seriesColors.length][isDark ? 'dark' : 'light'];
  });

const RISK_ORDER = { high: 0, elevated: 1, low: 2, '': 3 };
const state = { search: '', candidate: 'all', sort: 'date-desc', tab: 'findings' };

function escapeHtml(s) {
  return (s || '').toString()
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function matchesSearch(r, hayFields) {
  if (!state.search) return true;
  const hay = hayFields.join(' ').toLowerCase();
  return hay.includes(state.search.toLowerCase());
}

function sortFindings(rows) {
  const sorted = [...rows];
  if (state.sort === 'date-desc') sorted.sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
  else if (state.sort === 'date-asc') sorted.sort((a, b) => (a.published_at || '').localeCompare(b.published_at || ''));
  else if (state.sort === 'risk') sorted.sort((a, b) => (RISK_ORDER[a.risk_level] ?? 3) - (RISK_ORDER[b.risk_level] ?? 3));
  else if (state.sort === 'candidate') sorted.sort((a, b) => a.candidate_name.localeCompare(b.candidate_name));
  return sorted;
}

const dash = '<span class="muted-dash">&mdash;</span>';

function renderFindings() {
  let rows = records.filter(r => r.status === 'accepted');
  if (state.candidate !== 'all') rows = rows.filter(r => r.candidate_name === state.candidate);
  rows = rows.filter(r => matchesSearch(r, [r.title, r.source, r.candidate_name, r.topic, r.stance, r.matched_on, r.summary]));
  rows = sortFindings(rows);

  document.getElementById('count-findings').textContent = rows.length;

  const feed = document.getElementById('findings-feed');
  feed.innerHTML = rows.map(r => {
    const meta = [r.source, (r.published_at || '').slice(0, 10), r.collector].filter(Boolean).join(' &middot; ');
    const titleHtml = r.url
      ? `<a class="story-title" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>`
      : `<span class="story-title no-link">${escapeHtml(r.title)}</span>`;
    const color = candidateColor[r.candidate_id] || 'var(--border)';

    return `
    <div class="story-card" style="--candidate-color: ${color}">
      <div class="story-card-header">
        <span class="candidate-name">${escapeHtml(r.candidate_name)}</span>
        <span class="story-date">${(r.published_at || '').slice(0, 10)}</span>
        ${r.backfilled ? '<span class="badge backfilled">backfilled</span>' : ''}
      </div>
      ${titleHtml}
      ${r.summary ? `<div class="story-summary">${escapeHtml(r.summary)}</div>` : ''}
      <div class="story-footer">
        <span class="mini-badge">${escapeHtml(r.topic) || dash}</span>
        <span class="mini-badge stance-${escapeHtml(r.stance)}">${escapeHtml(r.stance) || dash}</span>
        <span class="mini-badge risk-${escapeHtml(r.risk_level)}">${escapeHtml(r.risk_level) || dash}</span>
        ${r.cluster_size > 1 ? `<span class="mini-badge">${r.cluster_size} outlets</span>` : ''}
        <span class="story-source">${meta}</span>
      </div>
    </div>`;
  }).join('');

  document.getElementById('findings-empty').style.display = rows.length ? 'none' : 'block';
}

function renderAudit() {
  let rows = records.filter(r => r.status === 'rejected');
  if (state.candidate !== 'all') rows = rows.filter(r => r.candidate_name === state.candidate);
  rows = rows.filter(r => matchesSearch(r, [r.title, r.candidate_name, r.reason, r.collector]));
  rows.sort((a, b) => a.candidate_name.localeCompare(b.candidate_name));

  document.getElementById('count-audit').textContent = rows.length;

  const tbody = document.getElementById('audit-rows');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${escapeHtml(r.candidate_name)}</td>
      <td>${escapeHtml(r.title)}</td>
      <td>${escapeHtml(r.collector)}</td>
      <td class="reason-text">${escapeHtml(r.reason)}</td>
    </tr>
  `).join('');

  document.getElementById('audit-empty').style.display = rows.length ? 'none' : 'block';
}

function render() {
  renderFindings();
  renderAudit();
}

document.getElementById('search').addEventListener('input', e => {
  state.search = e.target.value; render();
});
document.getElementById('filter-candidate').addEventListener('change', e => {
  state.candidate = e.target.value; render();
});
document.getElementById('sort-order').addEventListener('change', e => {
  state.sort = e.target.value; renderFindings();
});
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('view-' + tab.dataset.tab).classList.add('active');
  });
});

render();

// ---- Daily volume chart (hand-rolled SVG, no charting library) ----
function buildChart() {
  const accepted = records.filter(r => r.status === 'accepted' && r.published_at);
  const candidateIds = [...new Map(accepted.map(r => [r.candidate_id, r.candidate_name])).entries()];
  if (candidateIds.length === 0) return;

  const toDay = iso => iso.slice(0, 10);
  const allDays = accepted.map(r => toDay(r.published_at));
  const minDay = allDays.reduce((a, b) => a < b ? a : b);
  const maxDay = new Date().toISOString().slice(0, 10);

  const days = [];
  for (let d = new Date(minDay + 'T00:00:00Z'); d <= new Date(maxDay + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + 1)) {
    days.push(d.toISOString().slice(0, 10));
  }

  const counts = {};
  for (const [cid] of candidateIds) counts[cid] = {};
  for (const r of accepted) {
    const day = toDay(r.published_at);
    counts[r.candidate_id][day] = (counts[r.candidate_id][day] || 0) + 1;
  }

  const W = 900, H = 260, padL = 30, padR = 12, padT = 12, padB = 24;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const maxCount = Math.max(1, ...candidateIds.map(([cid]) => Math.max(0, ...days.map(d => counts[cid][d] || 0))));

  const x = i => padL + (days.length <= 1 ? 0 : (i / (days.length - 1)) * plotW);
  const y = v => padT + plotH - (v / maxCount) * plotH;

  const svg = document.getElementById('chart');
  const legend = document.getElementById('chart-legend');
  let svgHtml = '';

  // gridlines (y-axis, 4 steps)
  const steps = 4;
  for (let s = 0; s <= steps; s++) {
    const v = Math.round((maxCount / steps) * s);
    const yy = y(v);
    svgHtml += `<line x1="${padL}" y1="${yy}" x2="${W - padR}" y2="${yy}" class="crosshair" stroke-dasharray="0" opacity="0.5"/>`;
    svgHtml += `<text x="2" y="${yy + 3}">${v}</text>`;
  }

  // x-axis date ticks (about 6 labels across the range)
  const tickEvery = Math.max(1, Math.floor(days.length / 6));
  days.forEach((d, i) => {
    if (i % tickEvery === 0 || i === days.length - 1) {
      svgHtml += `<text x="${x(i)}" y="${H - 6}" text-anchor="middle">${d.slice(5)}</text>`;
    }
  });

  candidateIds.forEach(([cid, name], si) => {
    const color = seriesColors[si % seriesColors.length][isDark ? 'dark' : 'light'];
    const pts = days.map((d, i) => [x(i), y(counts[cid][d] || 0)]);
    const path = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
    svgHtml += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    // end marker + direct label
    const last = pts[pts.length - 1];
    svgHtml += `<circle cx="${last[0]}" cy="${last[1]}" r="4" fill="${color}" stroke="var(--bg)" stroke-width="2"/>`;
  });

  // invisible crosshair capture + line
  svgHtml += `<line id="crosshair-line" x1="0" y1="${padT}" x2="0" y2="${padT + plotH}" class="crosshair" style="display:none"/>`;
  svgHtml += `<rect x="${padL}" y="${padT}" width="${plotW}" height="${plotH}" fill="transparent" id="chart-hit"/>`;

  svg.innerHTML = svgHtml;

  legend.innerHTML = candidateIds.map(([cid, name], si) => {
    const color = seriesColors[si % seriesColors.length][isDark ? 'dark' : 'light'];
    return `<span class="key"><span class="line" style="background:${color}"></span>${escapeHtml(name)}</span>`;
  }).join('');

  const tooltip = document.getElementById('chart-tooltip');
  const hitRect = document.getElementById('chart-hit');
  const crosshairLine = document.getElementById('crosshair-line');

  hitRect.addEventListener('pointermove', e => {
    const rect = svg.getBoundingClientRect();
    const svgX = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((svgX - padL) / plotW) * (days.length - 1));
    const idx = Math.max(0, Math.min(days.length - 1, i));
    const day = days[idx];

    crosshairLine.style.display = 'block';
    crosshairLine.setAttribute('x1', x(idx));
    crosshairLine.setAttribute('x2', x(idx));

    tooltip.style.display = 'block';
    tooltip.style.left = Math.min(rect.width - 160, Math.max(0, (x(idx) / W) * rect.width + 8)) + 'px';
    tooltip.style.top = '8px';

    const rowsHtml = candidateIds.map(([cid, name], si) => {
      const color = seriesColors[si % seriesColors.length][isDark ? 'dark' : 'light'];
      const val = counts[cid][day] || 0;
      return `<div class="row"><span class="line" style="background:${color}"></span><span class="lbl">${escapeHtml(name)}</span><span class="val">${val}</span></div>`;
    }).join('');
    tooltip.innerHTML = `<div class="date">${day}</div>${rowsHtml}`;
  });
  hitRect.addEventListener('pointerleave', () => {
    tooltip.style.display = 'none';
    crosshairLine.style.display = 'none';
  });
}
buildChart();
</script>
</body>
</html>
"""


def generate() -> Path:
    records = load_records()
    candidates_full = _load_candidates_full()
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    series_colors_json = json.dumps(SERIES_COLORS)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    any_backfilled = any(r.get("backfilled") for r in records)
    backfill_note = (
        "includes backfilled history (collected_at approximated from each item's own publish date -- see backfill.py)"
        if any_backfilled
        else "no backfilled history yet"
    )

    name_cards = []
    for i, c in enumerate(candidates_full):
        color = SERIES_COLORS[i % len(SERIES_COLORS)]["light"]
        aliases = ", ".join(c.get("aliases") or []) or "(none configured)"
        name_cards.append(
            f'<div class="name-card">'
            f'<div class="cname"><span class="swatch" style="background:{color}"></span>{c["name"]}</div>'
            f'<div class="office">{c["office"]}</div>'
            f'<div class="aliases"><b>Aliases matched on:</b> {aliases}</div>'
            f"</div>"
        )

    html = (
        TEMPLATE.replace("__DATA_JSON__", data_json)
        .replace("__SERIES_COLORS_JSON__", series_colors_json)
        .replace("__GENERATED_AT__", generated_at)
        .replace("__BACKFILL_NOTE__", backfill_note)
        .replace("__NAME_CARDS__", "\n".join(name_cards))
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    return OUTPUT_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Wrote {path} ({len(load_records())} records)")
