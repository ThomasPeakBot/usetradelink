# TradeLink SEO research and content engine

The engine turns real search data into ranked, evidence-backed recommendations. Where the evidence justifies it, it prepares
content that a human approves in Slack before anything is published.

```
 GSC rows (seo-auditor) ─┐
 Semrush (budgeted) ─────┤                 ┌─ briefs (evidence, facts, competitor structure, link plan)
 competitor pages ───────┼─► signals ─► opportunity engine ─┤
 our pages (inventory) ──┘   (provenance     (decide action,    └─ draft (optional model) ─► quality gate ─► GitHub PR
 published changes ──────────► on every #)    priority, cooling-off)                                          │
                                                                                                              ▼
 dashboard (read-only) ◄── Postgres ◄── change tracking ◄── approval executor merges ◄── Slack Approve/Reject/Revise
```

## What each part does
| Module | Job | Paid or outward-facing? |
|---|---|---|
| `engine/inventory.py` | Parses our sitemap pages: title, headings, links, word count, dateModified, text | Reads usetradelink.com |
| `engine/providers/semrush.py` | Semrush Analytics v3: keyword metrics, related terms, questions, competitor organic keywords | **Paid.** Off by default, hard-capped |
| `engine/research.py` | Plans Semrush calls, skips anything refreshed recently, imports saved snapshots | — |
| `engine/competitors.py` | Snapshots competitor pages (title/headings/word count only); obeys robots.txt | Off unless `ENGINE_COMPETITOR_CRAWL=true` |
| `engine/signals.py` | Joins GSC, Semrush and competitor rankings per keyword, with the source and date of every number | — |
| `engine/opportunities.py` | Decides `new_page`, `improve_page`, `rewrite_title_meta`, `add_internal_links`, `consolidate_cannibalization`, `differentiate_pages`, `refresh_outdated` or `no_action` | — |
| `engine/briefs.py` | Brief per opportunity: evidence, real questions, ranking competitor pages, allowed/blocked facts, link plan | — |
| `engine/drafting.py` | Optional model draft (structured JSON), rendered into the site template with sanitised HTML | **Paid.** Off unless `CONTENT_LLM_ENABLED=true` |
| `engine/quality.py` | Gate: originality vs existing pages, stuffing, filler, forbidden claims, disputed facts, unsourced figures, competitor claims need source + date | — |
| `engine/proposals.py` | Opens a labelled PR (`seo-agent-proposal`) with only allowlisted paths; never merges | Off unless `ENGINE_PROPOSALS_ENABLED=true` |
| `engine/tracking.py` | Records changes the executor published; compares 28 days before vs days 7–34 after; labels small samples | — |
| `engine/dashboard.py` | Separate read-only internal dashboard (HTTP Basic auth, read-only transactions, no JS) | Internal |

## Decision rules (summary)
- **No evidence, no action.** A keyword needs Semrush volume or Search Console impressions.
- **Relevance is conservative.** Forex, MT4/5, crypto and stocks are excluded. Unqualified "copy trading" is low relevance.
  Navigational competitor brand searches never get their own page.
- **Pages are owned by intent.** A page owns a query only if its title, H1 or headings carry the query's specific words, so a
  Tradovate page can't "own" an Apex query.
- **Improve before adding.** If an existing page covers the cluster, the engine improves it rather than creating a near-duplicate.
- **Cannibalisation is flagged both ways:** from Search Console (several pages each get ≥20% of a cluster's impressions) and on-page
  (2+ pages whose title/H1 contain every word of a specific query).
- **Cooling-off.** A page changed in the last 28 days gets no new recommendation, so its last change can be measured.
- **Priority** = 100 × relevance × demand × winnability × intent weight × confidence × action weight. Every factor is stored with the
  opportunity and shown on the dashboard.

## Facts registry
`facts/tradelink_facts.json` is the only source of TradeLink product statements that content may use. Facts marked `disputed` or
`needs_confirmation` (currently NinjaTrader support and the "ten protection rules") are blocked until the product owner decides.
Change it only from an owner decision or a primary source.

## Running
```
pip install -r requirements.txt
export DATABASE_URL=postgresql://...              # same Postgres as seo-auditor / seo-approvals
python -m engine.run import-snapshot tests/fixtures/semrush-snapshot-2026-10-16.json
python -m engine.run --site-dir ../..             # or omit --site-dir to read the live site
python -m engine.run report
python -m engine.run audit-content --site-dir ../..
TEST_DATABASE_URL=postgresql://.../scratch python -m unittest discover -s tests -v
```

## Railway (two services, root directory `automation/engine`)
| Service | Config file | Variables |
|---|---|---|
| `seo-engine` (cron, daily 14:30 UTC after the 13:00 collector) | `railway.json` | `DATABASE_URL`; optional Semrush, crawl, LLM and proposal flags below |
| `seo-dashboard` (web, private) | `railway.dashboard.json` | `DATABASE_URL` (a read-only Postgres role is recommended), `DASHBOARD_USER`, `DASHBOARD_PASSWORD` (≥16 chars) |

Optional flags, all off by default:

| Flag | Default | Effect |
|---|---|---|
| `ENGINE_SEMRUSH_ENABLED=true` + `SEMRUSH_API_KEY` + `ENGINE_SEMRUSH_MAX_UNITS_PER_RUN` / `_PER_MONTH` | caps default to **0** | Hard ceilings 3,000/run and 10,000/month. The monthly cap also counts the collector's usage. Units are reserved before each call and actual units are read from Semrush's free balance endpoint. |
| `ENGINE_COMPETITOR_CRAWL=true` | off | ≤10 competitor pages per run, 7-day refresh, robots.txt respected |
| `CONTENT_LLM_ENABLED=true` + `ANTHROPIC_API_KEY` | off | Claude drafts new pages. Default model `claude-opus-5-5`, with server-side refusal fallback; usage is recorded in `engine_api_usage`. |
| `ENGINE_PROPOSALS_ENABLED=true` + `ENGINE_GITHUB_TOKEN` | off | Opens labelled PRs (≤3 open). The token needs Contents + Pull requests write on this repo only; it cannot merge. |

## Semrush cost reference
Observed 2026-10-16 via the Semrush MCP:

| Report | Units |
|---|---|
| `phrase_these` | 10 per keyword |
| `phrase_related` | ~93 per row |
| `phrase_questions` | ~110 per row |
| competitor organic | 10–66 per row |

The engine reserves the worst case. Prefer `phrase_these` batches; the refresh windows are 28 days for keyword metrics, 60 for related/questions, 30 for competitor rankings.
