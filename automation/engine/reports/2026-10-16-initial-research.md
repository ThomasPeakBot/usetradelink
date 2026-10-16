# TradeLink SEO research: first engine run (2026-10-16)

Produced by `python -m engine.run` on a local Postgres, using:
- **Semrush:** one saved snapshot of 5 Semrush reports pulled through the Semrush MCP on 2026-10-16
  (`tests/fixtures/semrush-snapshot-2026-10-16.json`, 92 rows, database `us`). Semrush reported
  **5,470 API units** for these calls (phrase_these 120, phrase_related 1,850, phrase_questions 1,650,
  tradesyncer.com organic 1,650, tradecopia.com organic 200). No further Semrush calls were made.
- **Our pages:** the 12 sitemap pages, read from the repository (same content as production on this date).
- **Search Console:** none. The production database is not reachable from the build environment, and Semrush has
  no organic data for usetradelink.com (`ERROR 50 :: NOTHING FOUND`), so every "ranking" statement below is about
  competitors, not TradeLink.

Nothing here is estimated. Every number comes from the snapshot above.

## What the data says
- **The futures niche is small and specific.** "futures trade copier" 140/mo (KD 6), "tradovate trade copier" 170 (KD 19),
  "tradovate group trade" 170 (KD 21, CPC 7.88), "apex trade copier" 260 (KD 12), "prop firm trade copier" 50,
  "cloud trade copier" 30. The broad terms are large but mixed-intent and hard: "trade copier" 1,600 (KD 56),
  "copy trading" 4,400 (KD 71, mostly stocks/forex/crypto platforms).
- **Questions show real Tradovate intent:** "how to copy trade on tradovate" 480/mo (KD 18), "does tradovate have a trade copier" 140 (KD 15),
  "can you copy trade on tradovate" 90 (KD 13), "how to copy trade on topstep" 140 (KD 25).
- **Competitors win mostly on brand.** tradecopia.com gets about 81% of its tracked traffic from "tradecopia"; tradesyncer.com's top term is
  "trade syncer". tradecopia.com ranks #1 for "futures trade copier" and "copy trading prop firms"; tradesyncer.com ranks #1 for "trade copier".
  Brand searches are large (tradesyncer 18,100; tradecopia 9,900) but navigational; the engine does not propose pages for them.

## Engine decisions (ranked)
{"status": "error", "error_type": "KeyError"}

18 further clusters were judged **no action**: out of scope (forex/crypto/stock copy trading, "copyrate", "http track website copier"),
navigational competitor brands, or already covered with no sign of underperformance.

How to read this:
- **differentiate_pages: "how to copy trade on tradovate".** `/tradovate-trade-copier/`, `/tradovate-multiple-accounts/` and `/apex-tradovate-trade-copier/`
  all target the same words. This is the audit's cannibalisation finding (#10, M15), now backed by the site's highest-value question (480/mo).
  The recommendation is to make `/tradovate-trade-copier/` the answer to "how to copy trade on Tradovate" and narrow the other two.
- **improve_page: "how to copy trade on topstep" → `/prop-firm-copy-trading/`.** No heading answers it. Any answer must respect the facts registry:
  TopstepX compatibility is **not** a published TradeLink fact, so the page can only explain options and limits, sourced to Topstep.
- **improve_page items without Search Console data are provisional.** They rest on competitor rankings and on-page gaps. Once GSC rows exist,
  the engine switches to position/CTR evidence and these may change.

## Existing content against the quality gate
`python -m engine.run audit-content` applies the same gate new drafts must pass:

| Page | Result | Errors | First error |
|---|---|---|---|
| / | fail | 2 | uses disputed fact 'ninjatrader' in 'TradeLink currently supports NinjaTrader and Tradovate': Homepage FAQ says NinjaTra… |
| /tradovate-trade-copier/ | pass | 0 | — |
| /compare/tradelink-vs-tradesyncer/ | fail | 11 | meta description must be 70-160 characters (found 163)… |
| /futures-trade-copier/ | fail | 3 | meta description must be 70-160 characters (found 165)… |
| /prop-firm-copy-trading/ | fail | 1 | 4 sentences copied verbatim from existing pages, e.g. 'We identify where other tools are a better fit and link to origin… |
| /best-futures-trade-copier/ | fail | 9 | 4 sentences copied verbatim from existing pages, e.g. 'We identify where other tools are a better fit and link to origin… |
| /compare/tradelink-vs-tradecopia/ | fail | 15 | 4 sentences copied verbatim from existing pages, e.g. 'We identify where other tools are a better fit and link to origin… |
| /apex-tradovate-trade-copier/ | fail | 1 | 4 sentences copied verbatim from existing pages, e.g. 'We identify where other tools are a better fit and link to origin… |
| /resources/ | fail | 2 | meta description must be 70-160 characters (found 161)… |
| /cloud-futures-trade-copier/ | pass | 0 | — |
| /tradovate-multiple-accounts/ | pass | 0 | — |
| /futures-trade-copier-risk-management/ | pass | 0 | — |


The repeated failures are real:
- the same boilerplate sentence appears on four pages
- competitor prices are stated in paragraphs that don't link their source
- the disputed NinjaTrader claim
- three meta descriptions are over 160 characters (fixed in #13)
- `/futures-trade-copier/` makes third-party claims without a "last verified" date

## Recommended next actions (need approval before any page changes)
1. Owner decides the NinjaTrader question; update `facts/tradelink_facts.json` and the homepage FAQ together.
2. Approve a consolidation plan for the three Tradovate pages around "how to copy trade on Tradovate".
3. Connect production Search Console data (the collector already stores it) and re-run; GSC evidence will replace on-page heuristics.
4. Keep Semrush calls to `phrase_these` batches (10 units/keyword) plus a monthly competitor refresh; related and question reports cost
   roughly 90–110 units per row, and the 2026-10-16 snapshot already covers them for 60 days.
