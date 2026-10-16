# TradeLink SEO intelligence — Phase 1

**Approval-first: this is a read-only collector.** It does not use GitHub write APIs, generate or publish website pages, commit to main, edit files, change affiliate links or request indexing.

## What it collects

- Daily crawl of up to 100 same-domain URLs listed in https://usetradelink.com/sitemap.xml.
- HTTP status, sitemap URLs that redirect, HTML title and meta description (missing or over 70/170 characters), exact canonical match (a missing or extra trailing slash is a mismatch), H1 count, noindex (meta or X-Robots-Tag) and internal link count.
- A run whose Search Console call failed is stored with `status = 'partial'`.
- Optional read-only Google Search Console page/query/day metrics for 28 finalized days, upserted into Postgres.
- Append-only technical audit runs and page snapshots in Postgres.

It does not estimate Semrush search volume, assert indexing status, or publish content.

## Setup

Railway: a separate project named tradelink-seo-agent, with Postgres and a scheduled worker built from the website GitHub repo. Set the worker root directory to /automation/seo and use the included railway.json. Set DATABASE_URL to the internal Postgres connection variable (e.g., reference the Postgres service variable) rather than a public database endpoint. Do not expose the worker as a public domain.

For Google Search Console:
1. Enable Search Console API in your Google Cloud project.
2. Create a dedicated service account with no Google Cloud project-wide editor/owner roles.
3. Add its email as a **Full user** (not Owner) to the Search Console domain property sc-domain:usetradelink.com. Phase 1 continues to request the read-only Search Console OAuth scope, regardless of account permission. A future separate, explicitly approved executor can request the `webmasters` read/write scope for supported Search Console operations, such as submitting a sitemap.
4. Put the service account JSON privately into Railway's GSC_SERVICE_ACCOUNT_JSON environment variable. Never commit it or paste it in chat.
5. Check the first scheduled job logs for gsc_status = ok. A new property may have little historical data.

Run locally in the automation/seo folder:
- python -m pip install -r requirements.txt
- python -m unittest discover -s tests (set TEST_DATABASE_URL to a disposable Postgres to include the database and Semrush budget tests)
- python main.py --dry-run

The normal run requires DATABASE_URL. Without Search Console credentials it still collects page audits and logs gsc_status = not_configured. Missing credentials are not a claim that Google data was collected.

## Reporting SQL

Latest audit runs:
SELECT * FROM seo_runs ORDER BY started_at DESC LIMIT 10;

Most common SEO flags (from page snapshots):
SELECT url, issues FROM seo_pages WHERE run_id = (SELECT id FROM seo_runs ORDER BY started_at DESC LIMIT 1);

Search performance:
SELECT page, sum(clicks) AS clicks, sum(impressions) AS impressions
FROM seo_search_performance WHERE data_date >= current_date - 28
GROUP BY page ORDER BY clicks DESC;

## Operational controls

- Approval is required for **every public website edit**: an opened PR is only a proposal. Today merges remain manual; a later approved action executor will be allowed to merge the exact PR commit SHA only after an explicit YES, run deployment verification, and report back. A NO must cause no publishing. No standing authorization to merge all PRs.
- The worker has no write token for the site repo and contains no site publication code.
- No generated content or affiliate claims are published in Phase 1.
- Review Railway cron logs daily while establishing reliability.
- Google Search Console data has a reporting delay; position/CTR must not drive daily copy churn.
- The database stores aggregate Search Console metrics, not customer account records.

## Future read/write permissions (not active in Phase 1)
The collector is permanently read-only and never needs GitHub write credentials. A separate action executor will hold scoped GitHub credentials to prepare branches and, only after explicit approval, merge specific PR SHAs. Search Console Full-user access plus the Google `webmasters` OAuth scope can permit approved sitemap actions. Search Console API does not publish public webpages. All code changes on the website still require an approved GitHub merge. Enable GitHub `main` branch protection before publishing automation is turned on.

## Optional Semrush integration (keyword intelligence)

Semrush Standard API is a paid, API-unit-metered integration. This worker uses the **v4 Keyword Metrics** endpoint only; it neither modifies Semrush nor your public website. **There are no Semrush requests unless SEMRUSH_ENABLED=true and a v4 key is present.**

1. Confirm eligible Semrush API subscription and API-unit allocation; do **not** purchase or upgrade without approval. Semrush MCP may be available on some lower plans, but MCP credentials are not the same as direct v4 API access to Railway.
2. In Semrush → Account → API Keys, obtain a **Version 4** key. Store it only in Railway `seo-auditor` → Variables as `SEMRUSH_API_KEY`, never in GitHub, URLs, logs, screenshots, Slack, or ChatGPT.
3. Initially set `SEMRUSH_ENABLED=false`. Once the plan, credentials, and budget are confirmed, enable `SEMRUSH_ENABLED=true`.
4. Budget controls: `SEMRUSH_MAX_UNITS_PER_RUN=120` (maximum 6 keywords/run) and `SEMRUSH_MAX_UNITS_PER_MONTH=600` (maximum 30 reserved requests/month). Hard ceilings in the code are 400/run and 2000/month. One v4 keyword metric request costs 20 API units at the documented pricing. The database records a 20-unit reservation **before every attempted request**, even if the call fails, preventing unbounded loops. There are no retries.
5. `SEMRUSH_SEED_KEYWORDS` is optional CSV of TradeLink-related targets. Initial defaults: futures trade copier, tradovate trade copier, best futures trade copier, prop firm trade copier, cloud futures trade copier, tradovate multiple accounts. The collector adds Search Console queries with at least five recent impressions, limited to relevant futures/trade copier topics; obvious forex/crypto terms are excluded.
6. To reduce spend, each keyword is fetched at most once per 28 days; the daily SEO crawl stays daily. All Semrush responses are stored as comparable keyword-level metrics in `seo_semrush_keyword_metrics`. The append-only usage log is `seo_semrush_api_usage`.
7. Inspect the Railway run's JSON `semrush.status`, `semrush.requests`, `semrush.reserved_units`, and Postgres tables. Missing keys or an unsupported subscription produce an error/status without exposing credentials. If you hit an API eligibility or unit error, leave `SEMRUSH_ENABLED=false` while resolving access.

**Contract check before enabling:** the request URL (`https://api.semrush.com/apis/v4/keywords/v1/metrics`), parameters and response shape in `semrush.py` have not been confirmed against Semrush's v4 reference from this repository (the v4 keyword reports are early access). Semrush documents v4 `cpc` in USD cents, which the parser divides by 100. Confirm the endpoint and the per-request unit cost with one manual request before raising the budget.

**IMPORTANT:** API units are not dollar amounts. This default is a low-cost trial cap in units, not an authorization to buy a Semrush plan or units. We do not generate, publish, or automatically approve site changes from these metrics.

Read-only SQL:

```sql
SELECT keyword,country,collected_on,search_volume,keyword_difficulty,cpc_usd,intents
FROM seo_semrush_keyword_metrics ORDER BY collected_on DESC, search_volume DESC LIMIT 50;
SELECT date_trunc('month',requested_at) AS month, SUM(units_reserved) AS units_reserved
FROM seo_semrush_api_usage GROUP BY 1 ORDER BY 1 DESC;
```
