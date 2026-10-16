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
- **Approval workflow:** Future recommendations and drafts -> GitHub draft PR -> tests/evidence -> explicit YES/NO human decision on a specific proposed change -> a scoped action executor merges exactly that approved PR and verifies the Cloudflare production result. A NO leaves the PR unmerged. No automated publishing from scheduled jobs or implicit approval; no direct-to-main pushes.
- **Security:** No secrets in GitHub. The Phase 1 collector keeps its read-only Google scope; for later optional Search Console writes (such as sitemap submission), a separate approval-gated action executor can use the `webmasters` read/write scope and a Search Console **Full user** account (not Owner). GitHub write credentials belong only to the scoped action executor, never the daily crawler. No public worker domain. Least-privilege credentials.

## Phase 1 — foundation
1. [x] Audit repo structure and hosting pattern.
2. [x] Establish dedicated Railway project and private Postgres.
3. [x] Add read-only crawler, GSC adapter, schema, CLI tests and operator docs on an unmerged branch.
4. [ ] Verify Railway worker startup and first successful stored audit.
5. [ ] Configure GSC service account and prove read-only API ingestion (Full user property access is permitted for future approved GSC actions; Phase 1 only requests readonly OAuth scope).
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
- Agent prepares PRs and performs all work. Human says YES/NO for a **specific immutable PR commit SHA**. If YES, the action executor rechecks unchanged SHA, tests, approved paths, and authorization, then merges and verifies Cloudflare; if NO, it does not merge. No separate manual GitHub merge is required after the approved executor is available. Until that executor is implemented and tested, PRs stay manual/unmerged.
- Approval expires on any new commit, safety-check failure, or materially changed scope; never infer consent from a past approval.
- Protect `main` with branch rules (PR required, checks, restricted pushes, no bypass/auto-merge) before enabling executor. Every approved action should be logged, and deployments must be reversible.
- Track content change timestamp and subsequent GSC performance with meaningful multiweek evaluation windows.

## Phase 4 — affiliate and conversion-aware optimization
- Record partner URL, relationship owner, disclosure, terms expiry and last verified date. Do not invent offers or claim sponsorship.
- Ensure sponsored link attributes, program compliance and working redirect tracking.
- Join GA4 conversion data and checkout purchase attribution only after access and consent are verified.
- Measure qualified traffic and conversions, not raw page count.

## Hard gates
- Approval before every public site update, including SEO pages, internal links, blogs, affiliate pages, sitemap and navigation. Approval may be given in chat or via a verified review UI and must identify the specific proposed change. Explicit rejection cancels it.
- Never edit or republish existing pricing/checkout scripts, product claims or brand assets without explicit review.
- Strict fail-closed checks if external source data unavailable.
- No bulk/low-quality AI content production or rankings guarantees.

## Future agent operations after explicit approval
- **Before approval:** scan keywords, research, write drafts, revise a feature branch, run CI, prepare a diff and a proposed change summary.
- **After approval of exact SHA:** merge the approved PR through a tightly scoped GitHub token; Cloudflare builds from `main`; verify pages, analytics and internal links; report result and rollback instructions.
- **Search Console changes:** API `webmasters` scope supports approved sitemap submissions and management, but does not publish website content or provide ordinary-page instant indexing. Proposals to remove a sitemap/property require separate explicit confirmation.
- **Rejection:** stop that proposed action; keep audit and proposal logs; do not publish anything.
- **Initial state:** executor not implemented; Phase 1 worker can never publish. Granting Google Full user does not itself turn on automation.
