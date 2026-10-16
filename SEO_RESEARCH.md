# TradeLink SEO / AIO research and content roadmap

_Last updated 2026-10-16. Semrush database: **US**. Raw exports are cached in [`research/semrush/`](research/semrush/). Reuse them before pulling again._

## 1. Technical audit: production status (blocker)

**None of this repository's pages are live.** Checked 2026-10-16 from this session:

| Check | Result |
|---|---|
| `usetradelink.com` DNS | Nameservers `ns39/ns40.domaincontrol.com` (GoDaddy). A records `15.197.225.128`, `3.33.251.168`, GoDaddy's forwarding service, not GitHub Pages |
| `https://usetradelink.com/` | **301 → `http://peakbot.com/tradelink`** (a downgrade to plain HTTP) → 301 `https://peakbot.com/tradelink` → 301 `/tradelink/` → 200 |
| `https://usetradelink.com/robots.txt`, `/sitemap.xml`, `/compare/…`, `/futures-trade-copier/` | 404 (the forwarder doesn't serve the repo) |
| `www.usetradelink.com` | CNAME `thomaspeakbot.` is **malformed** (should be `thomaspeakbot.github.io.`). Proxy returns 502 |
| `https://thomaspeakbot.github.io/usetradelink/` | 404, so GitHub Pages doesn't look enabled for this repo |
| Live TradeLink page | `https://peakbot.com/tradelink/` (WordPress), `<link rel="canonical" href="https://peakbot.com/tradelink/">` |
| Search Console | `usetradelink.com` has a `google-site-verification` TXT record, so a Domain property probably exists in someone's GSC |
| Checkout | `tlink.peakbot.com/checkout/price_1UDhh0BZsQfGLBPdlnScmYrH` → 302 `pay.peakbot.com/checkout/…?org=tradelink-by-peakbot` → 200. Query strings are preserved |

### Cutover checklist (owner, in GoDaddy and GitHub)
1. Repo **Settings → Pages**: deploy from `main` / root. The custom domain `usetradelink.com` is already in `CNAME`.
2. GoDaddy DNS: **turn off domain forwarding**. Set apex A records to `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`, and optionally AAAA `2606:50c0:8000::153` … `8003::153`. Change `www` CNAME to `thomaspeakbot.github.io.`
3. When the certificate is issued, tick **Enforce HTTPS** in Pages.
4. On WordPress, 301 `peakbot.com/tradelink/` → `https://usetradelink.com/`, or set that page's canonical to `https://usetradelink.com/`. Otherwise the two identical pages compete and Google may keep the peakbot.com URL.
5. In GSC, submit `https://usetradelink.com/sitemap.xml` and request indexing for the 4 URLs.
6. Rerun the production checks in §6.

Also confirm that the GA4/Ads tags still record correctly after the domain change (see `ANALYTICS.md`).

### Repo-level audit (fixed in this branch)
- Every indexable page has one `<title>`, one `<h1>`, a unique description, a self-referencing canonical, valid JSON-LD and an entry in the sitemap with `lastmod`. `tests/check_site.py` enforces these.
- Added a `noindex` `404.html` (GitHub Pages serves it for unknown paths) and `llms.txt` (a plain-language product summary for LLM crawlers).
- Content is server-delivered HTML. With JavaScript disabled, each SEO page renders 900–1,400 words plus its tables (`tests/browser-check.js`).
- No horizontal overflow at 375px or 1366px. Tables scroll inside their own container on mobile.
- Performance notes (not changed, to preserve brand assets): `assets/logo.png` is 178 KB at 1509×400 and is displayed at about 154px wide. A resized copy (same artwork, about 15 KB) would help LCP on the SEO pages. `TradeLinkLaunch.mp4` (6 MB, `preload="auto"`) loads on the homepage; `preload="metadata"` plus a poster image would save bandwidth on mobile.

### Content accuracy findings (owner should confirm)
- **NinjaTrader claim conflict.** The homepage FAQ says TradeLink supports "NinjaTrader and Tradovate". The live app (`tradelink.peakbot.com`) only offers a Tradovate OAuth connection, and its own meta description reads "Tradovate → Tradovate trade copying". The checkout org config lists `enabledBrokers: ["tradovate"]`. New and updated pages therefore say **"connects through a Tradovate login"** and don't claim NinjaTrader support. If NinjaTrader-platform users are supported because they trade Tradovate-backed accounts, state that explicitly on the homepage. If not, remove "NinjaTrader" from the homepage FAQ.
- The `$40/month`, `150 accounts` and no-trial facts match the live checkout (`amount 40.00 USD / month`, `tradelinkFollowerAccounts: 150`, `trialDays: null`).
- The TradeSyncer comparison was re-verified against TradeSyncer's pricing and help pages (2026-10-16). Added: yearly billing "saves up to 30%", the full platform list (Rithmic, NinjaTrader, Tradovate, TradingView, Volumetrica, dxFeed, TradeSea), "30+ prop firms", and the card-required 7-day trial charged on day 7. Removed the "emergency stop" claim because the TradeLink site doesn't publish it.

## 2. Semrush findings

Connected through the official Semrush MCP. API units reported per call: domain_rank 20 each; resource_organic 500 × 2; phrase_these 310; phrase_organic 300 × 3; phrase_questions (counter showed 1,210); list_projects 100. The total is about 3,500 units.

### Domains (US, `domain_rank`)
| Domain | Organic keywords | Est. organic traffic/mo | Notes |
|---|---|---|---|
| usetradelink.com | none (no data) | none | Not indexed / forwarded |
| peakbot.com/tradelink/ | none (no data) | none | No US rankings for the subfolder |
| tradesyncer.com | 1,846 | 22,035 | Semrush Rank 85,959; 9 paid keywords |
| tradecopia.com | 840 | 12,001 | Semrush Rank 150,292 |

TradeSyncer's traffic is mostly **branded** ("trade syncer" 5,400, "tradesyncer" 22,200 and many misspellings). Its non-branded traffic comes from the generic "trade copier" / "copy trading" head terms (KD 50–77) and from a set of **platform and prop-firm landing pages** with very low difficulty.

### Keyword opportunities (US volume / Keyword Difficulty)
Intent codes come from Semrush: commercial, informational, navigational, transactional.

| Keyword | Vol | KD | Who ranks now | Fit for TradeLink |
|---|---|---|---|---|
| how to copy trade on tradovate | 480 | 18 | n/a | **Supported**, informational with buying intent |
| tradovate trade copier | 170 | 19 | Tradovate help (#1), PickMyTrade, Tradecopia #6, TradeSyncer #7 | **Supported** |
| tradovate copy trading | 170 | 19 | n/a | **Supported** |
| does tradovate have a trade copier | 140 | 15 | n/a | **Supported**, answered directly |
| how to copy trades on tradovate | 140 | 18 | n/a | **Supported** |
| can you copy trade on tradovate | 90 | 13 | n/a | **Supported** |
| how to copy trade tradovate accounts | 50 | 16 | n/a | **Supported** |
| futures trade copier | 140 | 6 | Tradecopia #1, FutuCopy #2, TradeSyncer #3, QuantVPS blog | Core category; existing guide |
| tradesyncer reviews / review | 140 / 10 | 8 / 0 | n/a | Comparison page (no fake reviews) |
| tradesyncer cost / free trial / alternatives | 40 / 40 / 30 | 14 / 0 / 0 | n/a | Comparison page |
| how does trade copier work | 40 | 5 | n/a | Existing guide |
| copy trading prop firms / prop firm copy trading | 210 / 40 | 14 / 0 | TradeSyncer #2 | Future page (see roadmap) |
| best trade copier / best trade copier for prop firms | 140 / 50 | 12 / 6 | TradeSyncer #1–2 | Future, needs a fair multi-vendor page |
| tradecopier | 590 | 15 | TradeSyncer #2 | Category term; homepage/guide |
| ninjatrader trade copier | 260 | 10 | NT Ecosystem, Duplicate Account Actions, Replikanto, CrossTrade | **Blocked**: NinjaTrader support not confirmed |
| ninjatrader copy trader / NT8 trade copier | 90 / 70 | 14 / 14 | n/a | **Blocked**, same reason |
| apex trade copier / apex copier | 260 / 110 | 5–12 / 6 | TradeSyncer #1 | Only if Apex accounts on Tradovate are confirmed workable |
| topstepx trade copier / project x copy trader | 590 / 170 | 4 / 8 | TradeSyncer | **Not supported** (ProjectX) |
| rithmic trade copier | 70 | 11 | n/a | **Not supported yet** (Rithmic coming soon) |
| tradingview trade copier | 210 | 13 | TradeSyncer #1 | Not supported |
| tradecopia (brand) | 9,900 | 14 | Tradecopia | Navigational; "vs" page has little direct demand |
| replikanto (brand) | 2,400 | 7 | flowbots.ninja | NinjaTrader add-on; blocked by NT question |
| trade copier | 1,900 | 50 | TradeSyncer #1 | Head term; long-term |
| copy trading | 4,400 | 77 | n/a | Too broad and competitive (crypto/forex intent) |

These had no Semrush data or zero volume: "tradelink peakbot", "cheap trade copier", "ninjatrader account copier", "copy trades between accounts", "tradecopia alternative", "is copy trading allowed in prop firms". "tradelink" (720, KD 43) is ambiguous with other brands. "peakbot" gets 480/mo.

### SERP observations
- The Tradovate SERP is led by **Tradovate's own Group Trade article** and forum threads. Searchers want to know whether they need a third-party copier at all. The new Tradovate page answers that first, with a sourced Group Trade vs copier table.
- QuantVPS blog listicles and Reddit threads rank for every copier term. Being *cited* in those (outreach, community answers) is a realistic off-page win.
- TradeSyncer's one-page-per-platform/prop-firm pattern (`/tradovate-trade-copier`, `/apex-trade-copier`, `/topstepx-trade-copier`, `/trade-copier/ninjatrader/`) is the main source of its low-KD traffic.

## 3. Ranked content roadmap

Ranked by conversion intent × realistic difficulty × evidence × whether TradeLink can honestly serve the searcher.

| # | Page | Status | Target cluster (combined vol) | Why |
|---|---|---|---|---|
| 1 | `/tradovate-trade-copier/` | **Built** | Tradovate copy trading questions (~1,240/mo, KD 13–19) | Only connection TradeLink supports; low KD; high intent |
| 2 | `/compare/tradelink-vs-tradesyncer/` | **Improved** | tradesyncer reviews/cost/trial/alternatives (~260/mo, KD ≤ 14) + switchers | Matches the switch offer; captures competitor evaluators |
| 3 | `/futures-trade-copier/` | **Updated** | futures trade copier, how does a trade copier work (~180/mo, KD 5–6) | Category explainer, internal hub |
| 4 | Homepage | Linked to all | tradecopier, cloud based trade copier | Conversion page |
| 5 | `/prop-firm-copy-trading/` | Next | copy trading prop firms (~250/mo, KD ≤ 14) | Needs per-firm copy-trading policy research from official firm docs; must not overstate permissions |
| 6 | `/rithmic-trade-copier/` | **Wait for Rithmic launch** | rithmic trade copier (70, KD 11) | Publishing before support would mislead |
| 7 | `/apex-trade-copier/` | Wait for confirmation | apex trade copier/copier (~370, KD 5–12) | Only if Apex accounts on Tradovate work end to end |
| 8 | `/ninjatrader-trade-copier/` | **Blocked on NinjaTrader question** | ~510/mo incl. "how to copy trade ninjatrader", KD 0–14 | High value if support is real |
| 9 | `/compare/tradelink-vs-tradecopia/` | Later | Tradecopia brand halo | Only after reading Tradecopia's official pricing/docs; direct "vs" demand is unmeasured |

Not recommended: TopstepX/ProjectX, TradingView, forex or crypto "copy trading" pages, and mass prop-firm × platform permutations. TradeLink can't serve those searchers, and thin pages would hurt the domain.

## 4. AIO / LLM visibility
- Each page opens with a direct, quotable answer (`.answer` block), followed by a sourced table and FAQ that mirrors the `FAQPage` JSON-LD.
- `llms.txt` states the verified product facts: price, connection, capacity, offer and limitations.
- Honest limitation statements (Rithmic coming soon, Tradovate only, no standard trial) make the pages more trustworthy and quotable in AI answers to "which copier supports X?".
- Off-page: get TradeLink listed accurately in the copier roundups that rank (QuantVPS, TradoxVPS) and answer relevant Reddit and Tradovate forum threads transparently as PeakBot.

## 5. Semrush and Search Console linking (owner action)
- Semrush has one project: **peakbot.com** (ID 27804822: Site Audit, Position Tracking, SEO Ideas). None exists for `usetradelink.com`.
- The MCP can't link GA4 or GSC; Google OAuth consent has to be granted in the Semrush UI. After cutover:
  1. Create a Semrush project for `usetradelink.com`, then run **Site Audit** (crawl 4 pages plus 404 handling).
  2. In **Position Tracking** (US, desktop + mobile), track the keywords in §2 marked Supported or Core.
  3. In **Organic Traffic Insights**, connect the GA4 property that receives usetradelink.com data and the `usetradelink.com` GSC Domain property, signing in as the Google account that owns them.

## 6. Metrics to monitor after deployment
- **Indexing:** GSC Pages report showing 4/4 indexed. Watch for "Duplicate, Google chose different canonical" against peakbot.com/tradelink/.
- **Rankings** (Semrush Position Tracking): the Tradovate cluster, "futures trade copier", "tradesyncer reviews/alternatives". Target top 10 within 8–12 weeks of indexing.
- **GSC:** impressions, clicks and CTR per page. Rewrite titles if CTR is under 2% at positions 1–5.
- **GA4:** `signup_cta_click` and `begin_checkout` per landing page (`page_type`, `cta_location`), then `purchase` once checkout tracking is configured (ANALYTICS.md).
- **Conversion rate:** landing page → `begin_checkout` → `purchase`, by `utm_source` / organic.
- **Revenue attribution:** switch-offer redemptions vs organic comparison-page sessions.
