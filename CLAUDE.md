# Candidate Research Monitor

## What this is
A daily media monitor for 5 political candidates. It sweeps free news and social
sources, filters to genuine mentions, classifies sentiment and topic, and emails a
morning digest. Runs on GitHub Actions cron. Private repo.

## Hard constraints
- **$0/month, permanently.** Never add a dependency that requires payment or a
  credit card. If a task seems to need a paid API, stop and tell me instead of
  signing up for a trial.
- **Python 3.11+, standard library plus minimal deps.** requests, feedparser,
  pyyaml, jinja2. Ask before adding anything else.
- **No scraping of logged-in surfaces.** APIs and public RSS only. No Selenium or
  Playwright against Facebook, Instagram, X, or TikTok — it violates ToS and gets
  IP-banned within days.
- **Never commit secrets.** API keys come from environment variables locally and
  GitHub Encrypted Secrets in CI. `.env` goes in `.gitignore` from the first commit.

## Free sources (all confirmed no-cost, no card required)
| Source | Key needed | Notes |
|---|---|---|
| Google News RSS | no | Workhorse. One feed per candidate. |
| GDELT DOC 2.0 API | no | Ships a tone score per article — use it. |
| Local outlet RSS | no | Highest signal. I'll supply the outlet list. |
| Reddit API | OAuth (free) | 100 req/min |
| YouTube Data API | free quota | 10,000 units/day |
| Bluesky AT Protocol | free | Fully open |
| Meta **Ad** Library API | free | Political ads only — NOT the Content Library |

## Explicitly out of scope
- **X/Twitter** — no free tier exists as of 2026. Do not add it. Do not suggest
  scraper resellers.
- **Facebook/Instagram organic content** — Meta Content Library is gated to vetted
  academic researchers. The Ad Library is separate and is fine.
- **TikTok** — research API is academic-only.

## Architecture rules
- **Every collector returns the same `Item` shape.** Adding or removing a source must
  not require touching anything downstream.
```python
  Item = {
    "id", "source", "source_url", "author", "published_at", "collected_at",
    "title", "text", "engagement", "raw"   # always keep the original payload
  }
```
- **One collector failing must not kill the sweep.** Log the error, continue, and
  report which sources failed in the digest.
- **Storage is append-only JSONL** at `data/YYYY-MM.jsonl`. Never commit a SQLite
  binary — git stores a full copy on every commit and the repo bloats to gigabytes.
  If SQL is useful, build SQLite in memory at runtime from the JSONL.
- **Store the raw payload.** If a post is deleted, our copy may be the only record.

## Disambiguation is the hardest part — treat it as such
A hit counts only if the candidate's name appears **and** at least one
`require_any` term appears within ~200 words of it. Then an LLM pass makes the
final call with the office and district in context.

```yaml
- name: "Jane Doe"
  aliases: ["Jane A. Doe", "Rep. Doe"]
  office: "US House NC-04"
  require_any: ["North Carolina", "NC-04", "congress", "campaign"]
  exclude_any: ["obituary", "Jane Doe Institute"]
```

**Log every rejection with its reason** so I can audit what's being thrown away.
Silent over-filtering is worse than noise — I can skim past a false positive, but
I'll never know about the story you dropped.

## Classification (Phase 2)
- Gemini Flash-Lite free tier, **batched 25 items per call.** Batching is what makes
  $0 possible — one call per item exhausts the daily quota before 8am.
- Fallback chain: Gemini → Groq free tier → rules-based (GDELT tone + keywords).
  Log which tier produced each result.
- Do NOT use VADER or TextBlob. They misread political framing badly — "attacked the
  bill" scores negative even when it's positive framing from the candidate's own side.
- Classify on multiple axes: `stance_toward_candidate`, `topic`, `content_type`,
  `claim_type`, `risk_level`, `narrative_frame` (short free-text label).

## Deduplication
One wire story runs in hundreds of outlets. Cluster on `title + first 500 chars`
(simhash, ~0.85 threshold), keep one canonical item — **but store the cluster size.**
That count is the reach signal: a story in 300 outlets matters more than one in 3.

## Build phases — stop for review after each
1. Google News + GDELT → JSONL → terminal output. Prove disambiguation works.
2. Dedupe + classification + email digest. *This is the minimum useful system.*
3. Reddit, YouTube, Bluesky, Meta Ad Library collectors.
4. GitHub Actions wiring + velocity alerting (spike vs. 14-day rolling baseline).
5. Static HTML dashboard — last, because the email may be enough.

## GitHub Actions gotchas (Phase 4)
- Cron is **UTC only**. No timezone support.
- Never schedule on `:00` — queue delays are worst at the top of the hour. Use `:07`.
- Schedules **auto-disable after 60 days of repo inactivity**, and commits from the
  default `GITHUB_TOKEN` don't reliably count as activity. Commit with a PAT.
- Private repo free tier is 2,000 Linux min/month. This job uses ~150. Fine.

## Working style
- Ask before making assumptions about the race, the candidates, or my priorities.
- Show me the plan for anything non-obvious before implementing it.
- Small commits with clear messages.
- Don't build ahead. Finish the current phase and stop.
