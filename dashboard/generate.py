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
                    "match_type": item.get("match_type", ""),
                    "llm_reason": item.get("llm_reason", ""),
                    "backfilled": bool(item.get("backfilled", False)),
                    "summary": item.get("summary", ""),
                    "mention_type": item.get("mention_type", ""),
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
                    "match_type": "",
                    "llm_reason": "",
                    "backfilled": False,
                    "summary": "",
                    "mention_type": "",
                }
            )

    return records


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Candidate Research Monitor</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #fcfcfb; --page: #f9f9f7; --fg: #0b0b0b; --muted: #898781; --secondary: #52514e;
    --border: #e1e0d9; --axis: #c3c2b7;
    --accept: #0ca30c; --accept-bg: #e3f7e3;
    --reject: #d03b3b; --reject-bg: #fbe7e6;
    --row-hover: #f3f3f1; --input-bg: #ffffff; --card-bg: #ffffff;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.08);
    --shadow-md: 0 2px 6px rgba(0,0,0,0.1);
    --shadow-lg: 0 4px 12px rgba(0,0,0,0.12);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1a1a19; --page: #0d0d0d; --fg: #ffffff; --muted: #898781; --secondary: #c3c2b7;
      --border: #2c2c2a; --axis: #383835;
      --accept: #0ca30c; --accept-bg: #123312;
      --reject: #e66767; --reject-bg: #3a1414;
      --row-hover: #232322; --input-bg: #1a1a19; --card-bg: #1a1a19;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
      --shadow-md: 0 2px 6px rgba(0,0,0,0.4);
      --shadow-lg: 0 4px 12px rgba(0,0,0,0.5);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 2rem; background: var(--page); color: var(--fg);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  h1 {
    font-size: 1.75rem; font-weight: 700; margin: 0 0 0.25rem; letter-spacing: -0.01em;
    color: var(--fg);
  }
  h2 {
    font-size: 0.95rem; font-weight: 700; margin: 0 0 1rem; color: var(--fg);
    text-transform: uppercase; letter-spacing: 0.05em; color: var(--secondary);
  }
  .subtitle {
    color: var(--muted); font-size: 0.875rem; margin-bottom: 2rem; font-weight: 500;
  }
  .card {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px;
    padding: 1.5rem; margin-bottom: 1.75rem; box-shadow: var(--shadow-sm);
  }
  .summary { display: flex; gap: 2rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .stat { font-size: 0.85rem; color: var(--muted); }
  .stat b {
    color: var(--fg); font-size: 1.25rem; font-weight: 700; display: block;
    margin-bottom: 0.25rem;
  }
  .names-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 1rem;
  }
  .name-card {
    border: 1px solid var(--border); border-radius: 10px; padding: 1rem;
    background: var(--bg); box-shadow: var(--shadow-sm);
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .name-card:hover {
    border-color: var(--secondary); box-shadow: var(--shadow-md);
  }
  .name-card .swatch {
    display: inline-block; width: 12px; height: 12px; border-radius: 3px;
    margin-right: 0.6rem; vertical-align: middle;
  }
  .name-card .cname {
    font-weight: 700; font-size: 0.95rem; margin-bottom: 0.4rem;
    display: flex; align-items: center;
  }
  .name-card .office {
    color: var(--muted); font-size: 0.8rem; margin-bottom: 0.6rem;
    font-weight: 500;
  }
  .name-card .aliases {
    color: var(--secondary); font-size: 0.8rem; line-height: 1.4;
  }
  .name-card .aliases b { color: var(--fg); font-weight: 700; }
  .controls {
    display: flex; gap: 0.75rem; margin-bottom: 1.5rem; flex-wrap: wrap;
    align-items: center;
  }
  input[type=text], select {
    padding: 0.6rem 0.85rem; border: 1px solid var(--border); border-radius: 8px;
    background: var(--input-bg); color: var(--fg); font-size: 0.875rem;
    font-family: inherit; transition: border-color 0.15s, box-shadow 0.15s;
  }
  input[type=text]:focus, select:focus {
    outline: none; border-color: var(--secondary); box-shadow: 0 0 0 3px rgba(0,0,0,0.05);
  }
  @media (prefers-color-scheme: dark) {
    input[type=text]:focus, select:focus { box-shadow: 0 0 0 3px rgba(255,255,255,0.1); }
  }
  input[type=text] { flex: 1; min-width: 220px; }
  select { cursor: pointer; }
  a { color: inherit; }
  .empty {
    color: var(--muted); padding: 3rem 2rem; text-align: center;
    font-size: 0.95rem;
  }
  .muted-dash { color: var(--muted); }

  .tabs {
    display: flex; gap: 0; margin-bottom: 1.5rem; border-bottom: 2px solid var(--border);
  }
  .tab {
    padding: 0.8rem 1.25rem; font-size: 0.9rem; font-weight: 700;
    color: var(--muted); cursor: pointer; border-bottom: 3px solid transparent;
    margin-bottom: -2px; user-select: none; transition: color 0.15s, border-color 0.15s;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .tab:hover { color: var(--fg); }
  .tab.active { color: var(--fg); border-bottom-color: var(--fg); }
  .tab .count {
    color: var(--muted); font-weight: 600; margin-left: 0.5rem;
    display: inline-block;
  }
  .tab.active .count { color: var(--fg); }
  .view { display: none; }
  .candidate-pills {
    display: flex; gap: 0.75rem; margin-bottom: 1.25rem; flex-wrap: wrap;
  }
  .pill {
    font: inherit; padding: 0.5rem 1rem; border-radius: 999px;
    border: 1.5px solid var(--border); background: var(--card-bg);
    color: var(--secondary); font-size: 0.875rem; font-weight: 700;
    cursor: pointer; display: flex; align-items: center; gap: 0.5rem;
    transition: all 0.15s;
  }
  .pill:hover {
    border-color: var(--secondary); box-shadow: var(--shadow-sm);
  }
  .pill .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--pill-color, var(--muted)); flex-shrink: 0;
  }
  .pill.active {
    background: var(--pill-color, var(--fg)); border-color: var(--pill-color, var(--fg));
    color: #fff; box-shadow: var(--shadow-md);
  }
  .pill.active .dot { background: rgba(255,255,255,0.9); }
  .view.active { display: block; }

  /* Findings feed (accepted items) */
  .story-card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-left: 4px solid var(--candidate-color, var(--border));
    border-radius: 10px; padding: 1.25rem; margin-bottom: 1rem;
    box-shadow: var(--shadow-sm); transition: box-shadow 0.15s, border-color 0.15s;
  }
  .story-card:hover { box-shadow: var(--shadow-md); }
  .story-card-header {
    display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.6rem;
    flex-wrap: wrap;
  }
  .candidate-name {
    font-weight: 700; font-size: 0.8rem; color: var(--candidate-color, var(--fg));
    text-transform: uppercase; letter-spacing: 0.03em;
  }
  .story-date { color: var(--muted); font-size: 0.8rem; font-weight: 500; }
  .story-title {
    display: block; font-weight: 700; font-size: 1rem; color: var(--fg);
    text-decoration: none; line-height: 1.4; margin-bottom: 0.6rem;
  }
  .story-title:hover { text-decoration: underline; }
  .story-title.no-link { color: var(--fg); }
  .story-summary {
    color: var(--secondary); font-size: 0.875rem; margin-bottom: 0.75rem;
    line-height: 1.5;
  }
  .story-footer {
    display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;
    padding-top: 0.75rem; border-top: 1px solid var(--border);
  }
  .story-source {
    color: var(--muted); font-size: 0.8rem; margin-left: auto;
    font-weight: 500;
  }
  .badge {
    display: inline-block; padding: 0.25rem 0.65rem; border-radius: 999px;
    font-size: 0.75rem; font-weight: 700; white-space: nowrap;
    text-transform: uppercase; letter-spacing: 0.02em;
  }
  .badge.backfilled {
    color: var(--secondary); background: var(--border);
    font-weight: 600; text-transform: none;
  }
  .badge.llm-judged {
    color: #4a3aa7; background: #eae7f9;
    font-weight: 600; cursor: help;
  }
  @media (prefers-color-scheme: dark) {
    .badge.llm-judged { color: #9085e9; background: #241f38; }
  }
  .story-summary.llm-reason {
    color: var(--secondary); font-style: italic; padding: 0.75rem;
    background: var(--bg); border-radius: 6px; margin-bottom: 0.75rem;
  }
  .mini-badge {
    display: inline-block; padding: 0.3rem 0.6rem; border-radius: 6px;
    font-size: 0.75rem; font-weight: 700; background: var(--border);
    color: var(--secondary); text-transform: uppercase; letter-spacing: 0.02em;
  }
  .mini-badge.risk-elevated, .mini-badge.risk-high {
    background: var(--reject-bg); color: var(--reject);
  }
  .mini-badge.stance-favorable {
    background: var(--accept-bg); color: var(--accept);
  }
  .mini-badge.stance-unfavorable {
    background: var(--reject-bg); color: var(--reject);
  }
  .mini-badge.mention-type {
    background: var(--border); color: var(--secondary);
  }

  /* Audit log (rejected items) -- compact table, this is a debug/audit view not the primary read */
  .table-card { padding: 0; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  col.col-candidate { width: 160px; }
  col.col-title { width: auto; }
  col.col-collector { width: 110px; }
  col.col-reason { width: 220px; }
  th, td {
    text-align: left; padding: 0.75rem 1rem; border-bottom: 1px solid var(--border);
    vertical-align: top; font-size: 0.85rem;
  }
  th {
    color: var(--muted); font-weight: 700; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.05em; position: sticky;
    top: 0; background: var(--bg); white-space: nowrap;
  }
  tbody tr:hover { background: var(--row-hover); }
  .reason-text { color: var(--reject); font-size: 0.8rem; font-weight: 500; }
  .chart-wrap { position: relative; margin-top: 1rem; }
  .chart-legend {
    display: flex; gap: 1.5rem; margin-bottom: 1rem; flex-wrap: wrap;
  }
  .chart-legend .key {
    display: flex; align-items: center; gap: 0.6rem; font-size: 0.85rem;
    color: var(--secondary); font-weight: 600;
  }
  .chart-legend .key .line { width: 14px; height: 2px; border-radius: 1px; }
  .chart-tooltip {
    position: absolute; pointer-events: none; background: var(--card-bg);
    border: 1px solid var(--border); border-radius: 8px; padding: 0.75rem 0.9rem;
    font-size: 0.8rem; box-shadow: var(--shadow-lg); display: none; min-width: 150px; z-index: 5;
  }
  .chart-tooltip .date {
    color: var(--muted); font-size: 0.75rem; margin-bottom: 0.5rem;
    font-weight: 700; text-transform: uppercase; letter-spacing: 0.02em;
  }
  .chart-tooltip .row {
    display: flex; align-items: center; gap: 0.6rem; justify-content: space-between;
    margin-bottom: 0.4rem;
  }
  .chart-tooltip .row:last-child { margin-bottom: 0; }
  .chart-tooltip .row .line { width: 10px; height: 2px; flex-shrink: 0; }
  .chart-tooltip .row .val { font-weight: 700; margin-left: auto; }
  .chart-tooltip .row .lbl { color: var(--secondary); }
  svg text { fill: var(--muted); font-size: 10px; }
  .crosshair { stroke: var(--axis); stroke-width: 1; }

  /* Mobile: everything above assumes desktop width. Narrow screens need
     tighter spacing, stacked controls, a smaller type scale, and -- most
     importantly -- the audit table (fixed-width columns that add up to
     ~490px) needs to scroll horizontally within its own card instead of
     overflowing the page or getting silently clipped. */
  @media (max-width: 640px) {
    body { padding: 1rem; font-size: 13px; }
    h1 { font-size: 1.35rem; }
    .subtitle { margin-bottom: 1.25rem; }
    .card { padding: 1rem; margin-bottom: 1.25rem; border-radius: 10px; }
    .names-grid { grid-template-columns: 1fr; gap: 0.75rem; }
    .summary { gap: 1.25rem; }
    .tabs { gap: 0; }
    .tab { padding: 0.5rem 0.7rem; font-size: 0.8rem; }
    .candidate-pills { gap: 0.4rem; margin-bottom: 0.6rem; }
    .pill { padding: 0.4rem 0.7rem; font-size: 0.78rem; }
    .controls { flex-direction: column; align-items: stretch; gap: 0.6rem; }
    input[type=text] { min-width: 0; width: 100%; }
    select { width: 100%; }
    .story-card { padding: 0.85rem; margin-bottom: 0.6rem; }
    .story-card-header { gap: 0.4rem; }
    .story-title { font-size: 0.9rem; }
    .story-footer { gap: 0.35rem; }
    .story-source { margin-left: 0; width: 100%; order: 99; margin-top: 0.3rem; }
    .mini-badge { font-size: 0.68rem; padding: 0.12rem 0.45rem; }
    /* table { width: 100% } (desktop, above) is what actually broke this:
       under table-layout:fixed, a 100%-wide table forces its columns to
       fit inside the card's ~356px mobile width, so col-title's "auto"
       computes to 0px once the other three fixed-width columns already
       consume it all -- confirmed directly (col-title reported width:0px
       via getComputedStyle), and since overflowing cell text isn't
       clipped, the 0-width title column's text visually overlapped the
       collector column instead of scrolling. Overriding the table to a
       fixed total width wider than the card, with all four columns given
       explicit widths (including title, which desktop leaves as "auto"),
       is what actually makes .table-card's overflow-x:auto do anything --
       a scrollable-but-still-squished table isn't a fix if the squishing
       already destroyed a column. */
    table { width: 620px; }
    col.col-candidate { width: 120px; }
    col.col-title { width: 250px; }
    col.col-collector { width: 90px; }
    col.col-reason { width: 160px; }
    th, td { padding: 0.6rem 0.75rem; font-size: 0.8rem; }
    .chart-wrap svg { min-width: 560px; }
    .chart-wrap { overflow-x: auto; }
  }
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

<div class="candidate-pills" id="candidate-pills">
  <button class="pill active" data-candidate="all">All candidates</button>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search title, source, candidate, summary...">
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
<script type="application/json" id="roster">__ROSTER_JSON__</script>
<script>
const records = JSON.parse(document.getElementById('data').textContent);
const seriesColors = JSON.parse(document.getElementById('series-colors').textContent);
const roster = JSON.parse(document.getElementById('roster').textContent); // all tracked candidates, even with 0 findings
const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;

const candidateColor = {};
roster.forEach(([cid], i) => {
  candidateColor[cid] = seriesColors[i % seriesColors.length][isDark ? 'dark' : 'light'];
});

const pillsContainer = document.getElementById('candidate-pills');
roster.forEach(([cid, name]) => {
  const btn = document.createElement('button');
  btn.className = 'pill';
  btn.dataset.candidate = name;
  btn.style.setProperty('--pill-color', candidateColor[cid]);
  btn.innerHTML = `<span class="dot"></span>${escapeHtml(name)}`;
  pillsContainer.appendChild(btn);
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
        ${r.match_type === 'llm_judged' ? `<span class="badge llm-judged" title="${escapeHtml(r.llm_reason)}">AI-verified match</span>` : ''}
      </div>
      ${titleHtml}
      ${r.match_type === 'llm_judged' ? `<div class="story-summary llm-reason"><b>Why this matched:</b> ${escapeHtml(r.llm_reason)}</div>` : ''}
      ${r.summary ? `<div class="story-summary">${escapeHtml(r.summary)}</div>` : ''}
      <div class="story-footer">
        <span class="mini-badge">${escapeHtml(r.topic) || dash}</span>
        <span class="mini-badge stance-${escapeHtml(r.stance)}">${escapeHtml(r.stance) || dash}</span>
        <span class="mini-badge risk-${escapeHtml(r.risk_level)}">${escapeHtml(r.risk_level) || dash}</span>
        ${r.mention_type ? `<span class="mini-badge mention-type">${escapeHtml(r.mention_type)}</span>` : ''}
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
document.querySelectorAll('.pill').forEach(pill => {
  pill.addEventListener('click', () => {
    document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    state.candidate = pill.dataset.candidate;
    render();
  });
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
  // Always show every tracked candidate, even ones with zero findings so far
  // (a candidate with nothing yet shouldn't silently vanish from the chart).
  const candidateIds = roster;
  if (candidateIds.length === 0) return;

  const toDay = iso => iso.slice(0, 10);
  const allDays = accepted.map(r => toDay(r.published_at));
  const maxDay = new Date().toISOString().slice(0, 10);
  const minDay = allDays.length ? allDays.reduce((a, b) => a < b ? a : b) : maxDay;

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
    roster_json = json.dumps([[c["id"], c["name"]] for c in candidates_full])
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
        .replace("__ROSTER_JSON__", roster_json)
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
