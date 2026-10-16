# TradeLink SEO automation plan — approval-first

## Baseline (16 October 2026)
- Public static GitHub repo: ThomasPeakBot/usetradelink, default branch main, existing Cloudflare website.
- Live XML sitemap was submitted successfully to Google Search Console and showed 12 discovered URLs.
- Homepage indexed; three high-intent pages passed Google's live accessibility test and received indexing requests. Indexing of those three is not yet confirmed.
- Existing site already has content guides, comparison pages, a resources hub, sitemap.xml and robots.txt.
- Existing checkout, tracking, logo, content and Cloudflare deployment must remain untouched until an explicitly approved PR.

## Architecture
- **Public website:** Cloudflare (unchanged), from the existing GitHub repo.
- **Intelligence service:** Railway project tradelink-seo-agent, cron worker reading public sitemap/site and optional Google Search Console read-only API.
- **Storage:** Railway private Postgres, append-only crawl snapshots plus GSC keyword/page/day aggregates.
- **Approval workflow:** Future recommendations and drafts -> GitHub draft PR -> tests/evidence -> human approval -> manual merge -> production checks. Never autonomous merge, direct-to-main pushes, or changes to checkout/tracking.
- **Security:** No secrets in GitHub. Dedicated GSC restricted service account. No public worker domain. Least-privilege credentials.

## Phase 1 — foundation
1. [x] Audit repo structure and hosting pattern.
2. [x] Establish dedicated Railway project and private Postgres.
3. [x] Add read-only crawler, GSC adapter, schema, CLI tests and operator docs on an unmerged branch.
4. [ ] Verify Railway worker startup and first successful stored audit.
5. [ ] Configure restricted GSC service account and prove API ingestion.
6. [ ] Verify stable seven-day operation before activating content generation.

## Phase 2 — suggestions, no publish
- Keyword opportunity scoring using GSC impressions/position, cluster fit, conversion relevance and optional Semrush keyword difficulty/search volume.
- Track canonical URL ownership and near-duplicate search intent to prevent keyword cannibalization.
- Audit content freshness, real affiliate relationships, supported brokers/prop firms, source links and product claims.
- Create reviewed recommendations (not live changes) for content, internal linking, refreshes and new landing pages.
- Capture current baseline before each proposed content change.

## Phase 3 — draft PR generator
- One narrowly scoped PR per topic cluster; no direct website mutations.
- Verify meaningful unique content, factual citations, canonical URLs, robots and sitemap, JSON-LD, accessibility, internal links, checkout safety and source verifiability.
- Human reviews and manually merges each PR. Explicitly disable auto-merge and any content publishing bypass.
- Track content change timestamp and subsequent GSC performance with meaningful multiweek evaluation windows.

## Phase 4 — affiliate and conversion-aware optimization
- Record partner URL, relationship owner, disclosure, terms expiry and last verified date. Do not invent offers or claim sponsorship.
- Ensure sponsored link attributes, program compliance and working redirect tracking.
- Join GA4 conversion data and checkout purchase attribution only after access and consent are verified.
- Measure qualified traffic and conversions, not raw page count.

## Hard gates
- Approval before every public site update.
- Never edit or republish existing pricing/checkout scripts, product claims or brand assets without explicit review.
- Strict fail-closed checks if external source data unavailable.
- No bulk/low-quality AI content production or rankings guarantees.
