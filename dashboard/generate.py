"""Builds a static, self-contained, searchable/sortable HTML dashboard from
the current data/*.jsonl files. No server, no network requests -- open
dashboard/index.html directly in a browser (file:// works fine).

This is a view of Phase 1 data only: raw accepted/rejected items. It has no
stance/topic/cluster-size/velocity columns because those are Phase 2-4
outputs (classification, dedupe, alerting) that don't exist yet.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config" / "candidates.yaml"
OUTPUT_PATH = Path(__file__).resolve().parent / "index.html"


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


def _load_candidate_map() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {c["id"]: {"name": c["name"], "office": c["office"]} for c in data["candidates"]}


def load_records() -> list:
    candidates = _load_candidate_map()
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
    --bg: #ffffff; --fg: #1a1a1a; --muted: #6b7280; --border: #e5e7eb;
    --accept: #15803d; --accept-bg: #dcfce7;
    --reject: #b91c1c; --reject-bg: #fee2e2;
    --row-hover: #f3f4f6; --input-bg: #ffffff;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0f1115; --fg: #e5e7eb; --muted: #9ca3af; --border: #2a2d34;
      --accept: #4ade80; --accept-bg: #14301f;
      --reject: #f87171; --reject-bg: #3a1414;
      --row-hover: #1a1d24; --input-bg: #1a1d24;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 1.5rem; background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 14px;
  }
  h1 { font-size: 1.25rem; margin: 0 0 0.25rem; }
  .subtitle { color: var(--muted); font-size: 0.85rem; margin-bottom: 1rem; }
  .summary { display: flex; gap: 1.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  .stat { font-size: 0.85rem; color: var(--muted); }
  .stat b { color: var(--fg); font-size: 1rem; }
  .controls {
    display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap;
  }
  input[type=text], select {
    padding: 0.45rem 0.6rem; border: 1px solid var(--border); border-radius: 6px;
    background: var(--input-bg); color: var(--fg); font-size: 0.85rem;
  }
  input[type=text] { flex: 1; min-width: 200px; }
  table { width: 100%; border-collapse: collapse; overflow-x: auto; display: block; }
  thead, tbody { display: table; width: 100%; table-layout: fixed; }
  th, td {
    text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border);
    vertical-align: top; word-break: break-word;
  }
  th {
    cursor: pointer; user-select: none; color: var(--muted);
    font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.03em;
    position: sticky; top: 0; background: var(--bg);
  }
  th:hover { color: var(--fg); }
  th.sorted::after { content: attr(data-arrow); margin-left: 0.3rem; }
  tbody tr:hover { background: var(--row-hover); }
  .badge {
    display: inline-block; padding: 0.15rem 0.5rem; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600;
  }
  .badge.accepted { color: var(--accept); background: var(--accept-bg); }
  .badge.rejected { color: var(--reject); background: var(--reject-bg); }
  a { color: inherit; }
  .empty { color: var(--muted); padding: 2rem; text-align: center; }
</style>
</head>
<body>
<h1>Candidate Research Monitor</h1>
<div class="subtitle">Phase 1 raw data — no classification, dedupe, or velocity signals yet. Generated __GENERATED_AT__.</div>

<div class="summary">
  <div class="stat"><b id="stat-total">0</b> total</div>
  <div class="stat"><b id="stat-accepted">0</b> accepted</div>
  <div class="stat"><b id="stat-rejected">0</b> rejected</div>
</div>

<div class="controls">
  <input type="text" id="search" placeholder="Search title, source, candidate, reason...">
  <select id="filter-status">
    <option value="all">All statuses</option>
    <option value="accepted">Accepted</option>
    <option value="rejected">Rejected</option>
  </select>
  <select id="filter-candidate">
    <option value="all">All candidates</option>
  </select>
</div>

<table>
  <thead>
    <tr>
      <th data-key="status">Status</th>
      <th data-key="candidate_name">Candidate</th>
      <th data-key="title">Title</th>
      <th data-key="source">Source</th>
      <th data-key="collector">Collector</th>
      <th data-key="published_at">Published</th>
      <th data-key="reason">Reason</th>
    </tr>
  </thead>
  <tbody id="rows"></tbody>
</table>
<div class="empty" id="empty-state" style="display:none">No rows match.</div>

<script type="application/json" id="data">__DATA_JSON__</script>
<script>
const records = JSON.parse(document.getElementById('data').textContent);

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
      const hay = [r.title, r.source, r.candidate_name, r.reason, r.collector]
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

  const tbody = document.getElementById('rows');
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td><span class="badge ${r.status}">${r.status}</span></td>
      <td>${escapeHtml(r.candidate_name)}</td>
      <td>${r.url ? `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.title)}</a>` : escapeHtml(r.title)}</td>
      <td>${escapeHtml(r.source)}</td>
      <td>${escapeHtml(r.collector)}</td>
      <td>${escapeHtml((r.published_at || '').slice(0, 10))}</td>
      <td>${escapeHtml(r.reason)}</td>
    </tr>
  `).join('');

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
</script>
</body>
</html>
"""


def generate() -> Path:
    records = load_records()
    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    html = TEMPLATE.replace("__DATA_JSON__", data_json).replace("__GENERATED_AT__", generated_at)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    return OUTPUT_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Wrote {path} ({len(load_records())} records)")
