# TradeLink website and SEO automation

Static site for usetradelink.com (Cloudflare Pages) plus SEO automation under `automation/`.

## Rules for assistants working in this repository
- **Semrush costs real money.** `mcp__Semrush__execute_report` is denied in `.claude/settings.json`; a research
  pull on 2026-10-16 used 5,470 API units. Use saved snapshots (`automation/engine/tests/fixtures/`) and the
  engine's budgeted client. Any new paid pull needs the owner's explicit approval with a unit estimate first
  (`phrase_these` ≈10 units/keyword; related/questions ≈90–110 units/row).
- **No publishing without approval.** Never merge to `main`, change Cloudflare/Railway/GitHub settings, set
  `PUBLISHING_ENABLED=true`, or enable `CONTENT_LLM_ENABLED`, `ENGINE_SEMRUSH_ENABLED` or proposals unless the
  owner asked for that specific action.
- **Product claims** come only from `automation/engine/facts/tradelink_facts.json` (`published` entries).
  Disputed items (NinjaTrader support, "ten protection rules") must not be stated.
- Only website files are published: `scripts/build_site.py` builds `_site/`. Never add secrets or private notes
  under a public file type outside `automation/`, `docs/`, `scripts/`, `tests/`.

## Checks
- Site: `python3 scripts/check_site.py && python3 -m unittest discover -s tests`
- Services: `python -m unittest discover -s tests` in `automation/seo`, `automation/approvals`, `automation/engine`
  (set `TEST_DATABASE_URL` to a disposable Postgres for database tests).
- Operations runbooks: `docs/ops/`.
