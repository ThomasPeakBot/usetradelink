# TradeLink SEO intelligence — Phase 1

**Approval-first: this is a read-only collector.** It does not use GitHub write APIs, generate or publish website pages, commit to main, edit files, change affiliate links or request indexing.

## What it collects

- Daily crawl of up to 100 same-domain URLs listed in https://usetradelink.com/sitemap.xml.
- HTTP status, HTML title, meta description, canonical, H1 count, basic noindex detection and internal link count.
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
- python -m unittest discover -s tests
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
