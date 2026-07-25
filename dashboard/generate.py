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
  .table-card { padding: 0; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  col.col-status { width: 100px; }
  col.col-candidate { width: 150px; }
  col.col-story { width: auto; }
  col.col-matched { width: 190px; }
  col.col-class { width: 150px; }
  col.col-outlets { width: 70px; }
  th, td {
    text-align: left; padding: 0.65rem 0.9rem; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  th {
    cursor: pointer; user-select: none; color: var(--muted);
    font-weight: 600; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em;
    position: sticky; top: 0; background: var(--bg); white-space: nowrap;
  }
  th:hover { color: var(--fg); }
  th.sorted::after { content: attr(data-arrow); margin-left: 0.3rem; }
  tbody tr:hover { background: var(--row-hover); }
  tbody td { font-size: 0.85rem; }
  .badge {
    display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
    font-size: 0.68rem; font-weight: 600; white-space: nowrap;
  }
  .badge.accepted { color: var(--accept); background: var(--accept-bg); }
  .badge.rejected { color: var(--reject); background: var(--reject-bg); }
  .badge.backfilled { color: var(--secondary); background: var(--border); font-weight: 500; }
  .status-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 0.3rem; }
  a { color: inherit; }
  .empty { color: var(--muted); padding: 2rem; text-align: center; }
  .muted-dash { color: var(--muted); }
  .story-title {
    display: block; font-weight: 600; color: var(--fg); text-decoration: none;
    line-height: 1.35; margin-bottom: 0.2rem;
  }
  .story-title:hover { text-decoration: underline; }
  .story-title.no-link { color: var(--fg); }
  .story-meta { color: var(--muted); font-size: 0.76rem; }
  .story-summary { color: var(--secondary); font-size: 0.78rem; margin-top: 0.3rem; line-height: 1.4; }
  .reason-text { color: var(--reject); font-size: 0.78rem; }
  .matched-name { font-weight: 600; font-size: 0.82rem; }
  .matched-context { color: var(--muted); font-size: 0.74rem; margin-top: 0.15rem; }
  .mini-badges { display: flex; flex-direction: column; gap: 0.25rem; align-items: flex-start; }
  .mini-badge {
    display: inline-block; padding: 0.1rem 0.45rem; border-radius: 5px;
    font-size: 0.7rem; background: var(--border); color: var(--secondary);
  }
  .mini-badge.risk-elevated, .mini-badge.risk-high { background: var(--reject-bg); color: var(--reject); }
  .mini-badge.stance-favorable { background: var(--accept-bg); color: var(--accept); }
  .mini-badge.stance-unfavorable { background: var(--reject-bg); color: var(--reject); }
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

<div class="summary">
  <div class="stat"><b id="stat-total">0</b> total</div>
  <div class="stat"><b id="stat-accepted">0</b> accepted</div>
  <div class="stat"><b id="stat-rejected">0</b> rejected</div>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search title, source, candidate, reason, matched name...">
  <select id="filter-status">
    <option value="all">All statuses</option>
    <option value="accepted">Accepted</option>
    <option value="rejected">Rejected</option>
  </select>
  <select id="filter-candidate">
    <option value="all">All candidates</option>
  </select>
</div>

<div class="card table-card">
<table>
  <colgroup>
    <col class="col-status"><col class="col-candidate"><col class="col-story">
    <col class="col-matched"><col class="col-class"><col class="col-outlets">
  </colgroup>
  <thead>
    <tr>
      <th data-key="status">Status</th>
      <th data-key="candidate_name">Candidate</th>
      <th data-key="published_at">Story</th>
      <th data-key="matched_on">Matched on</th>
      <th data-key="risk_level">Classification</th>
      <th data-key="cluster_size">Outlets</th>
    </tr>
  </thead>
  <tbody id="rows"></tbody>
</table>
<div class="empty" id="empty-state" style="display:none">No rows match.</div>
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

let sortKey = 'published_at';
let sortDir = -1;
const state = { search: '', status: 'all', candidate: 'all' };

function escapeHtml(s) {
  return (s || '').toString()
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function render() {
  let rows = records.filter(r => {
    if (state.status !== 'all' && r.status !== state.status) return false;
    if (state.candidate !== 'all' && r.candidate_name !== state.candidate) return false;
    if (state.search) {
      const hay = [r.title, r.source, r.candidate_name, r.reason, r.collector, r.topic, r.stance, r.matched_on, r.summary]
        .join(' ').toLowerCase();
      if (!hay.includes(state.search.toLowerCase())) return false;
    }
    return true;
  });

  rows.sort((a, b) => {
    const av = (a[sortKey] || '').toString();
    const bv = (b[sortKey] || '').toString();
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return 0;
  });

  document.getElementById('stat-total').textContent = records.length;
  document.getElementById('stat-accepted').textContent = records.filter(r => r.status === 'accepted').length;
  document.getElementById('stat-rejected').textContent = records.filter(r => r.status === 'rejected').length;

  const dash = '<span class="muted-dash">&mdash;</span>';

  const tbody = document.getElementById('rows');
  tbody.innerHTML = rows.map(r => {
    const meta = [r.source, (r.published_at || '').slice(0, 10), r.collector].filter(Boolean).join(' &middot; ');
    const titleHtml = r.url
      ? `<a class="story-title" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>`
      : `<span class="story-title no-link">${escapeHtml(r.title)}</span>`;

    const matchedCell = r.status === 'accepted'
      ? `<div class="matched-name">${escapeHtml(r.matched_on) || dash}</div>${r.matched_context ? `<div class="matched-context">near: ${escapeHtml(r.matched_context)}</div>` : ''}`
      : `<div class="reason-text">${escapeHtml(r.reason)}</div>`;

    const classCell = r.status === 'accepted'
      ? `<div class="mini-badges">
           <span class="mini-badge">${escapeHtml(r.topic) || dash}</span>
           <span class="mini-badge stance-${escapeHtml(r.stance)}">${escapeHtml(r.stance) || dash}</span>
           <span class="mini-badge risk-${escapeHtml(r.risk_level)}">${escapeHtml(r.risk_level) || dash}</span>
         </div>`
      : dash;

    return `
    <tr>
      <td><div class="status-cell"><span class="badge ${r.status}">${r.status}</span>${r.backfilled ? '<span class="badge backfilled">backfilled</span>' : ''}</div></td>
      <td>${escapeHtml(r.candidate_name)}</td>
      <td>
        ${titleHtml}
        <div class="story-meta">${meta || dash}</div>
        ${r.summary ? `<div class="story-summary">${escapeHtml(r.summary)}</div>` : ''}
      </td>
      <td>${matchedCell}</td>
      <td>${classCell}</td>
      <td>${r.cluster_size > 1 ? r.cluster_size : dash}</td>
    </tr>
  `;
  }).join('');

  document.getElementById('empty-state').style.display = rows.length ? 'none' : 'block';

  document.querySelectorAll('th[data-key]').forEach(th => {
    th.classList.toggle('sorted', th.dataset.key === sortKey);
    th.dataset.arrow = sortDir === 1 ? '\\u2191' : '\\u2193';
  });
}

document.getElementById('search').addEventListener('input', e => {
  state.search = e.target.value; render();
});
document.getElementById('filter-status').addEventListener('change', e => {
  state.status = e.target.value; render();
});
document.getElementById('filter-candidate').addEventListener('change', e => {
  state.candidate = e.target.value; render();
});
document.querySelectorAll('th[data-key]').forEach(th => {
  th.addEventListener('click', () => {
    if (sortKey === th.dataset.key) { sortDir *= -1; }
    else { sortKey = th.dataset.key; sortDir = 1; }
    render();
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
