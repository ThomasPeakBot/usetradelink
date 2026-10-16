# Production activation checklist

Do these in order. Each step is safe to stop after. Publishing stays off until step 15.

**Status key**

| Status | Meaning |
|---|---|
| READY | Done in code; nothing to change |
| NEEDS CONFIGURATION | A settings change that needs no spend or content decision |
| NEEDS MY APPROVAL | Affects the live site, costs money or changes product claims |
| BLOCKED | Cannot be checked or done from the automation session; the owner must do or confirm it |

## Already in code (READY)

| What | Where |
|---|---|
| Public-only build (`_site` allowlist; private files refused) | `scripts/build_site.py`, #18 |
| Real deployment check of every page, asset, header, and 404, plus private-file exposure on previews and production | `.github/workflows/deploy-verify.yml`, #18 |
| Approval gateway: exact-SHA merge; runtime branch-protection and check-provenance verification; replay protection; dev-branch deploys can't arm | `automation/approvals`, #19 |
| Semrush is fail-closed: caps default to 0, kill switch, shared DB pause, auto-pause when cost exceeds the estimate, one run at a time, and the month cap counts both services | #19 |
| Assistant sessions can't run billable Semrush reports | `.claude/settings.json` denies `mcp__Semrush__execute_report` |
| Research engine, quality gate, briefs, change tracking, read-only dashboard | `automation/engine`, #19 |

## Ordered steps

| # | Step | Status | Where | Exactly what to change | What could break | How to verify | Rollback |
|---|---|---|---|---|---|---|---|
| 1 | Publish only `_site` | **NEEDS MY APPROVAL** | Cloudflare → Workers & Pages → `usetradelink` → Settings → Builds | Build command `python3 scripts/build_site.py`; output directory `_site` | Builds of `main` fail until #18 merges, because `main` has no build script yet. Production keeps serving its last good deployment | On #18, retry the Cloudflare build, then re-run the `deploy-verify / cloudflare-preview` job: all 41 checks pass, including the 17 private files that currently return 200 | Clear the build command; set output back to `/` |
| 2 | Limit public branch previews (optional) | NEEDS CONFIGURATION | Same page → Branch control | Preview deployments: "Custom branches" or "None" | No preview URLs for some branches, which turns `deploy-verify` preview checks to "skipped" | A new branch push creates no preview | Set back to "All non-production branches" |
| 3 | Merge the site release #18 | **NEEDS MY APPROVAL** | GitHub PR #18 | Merge (squash or rebase; `main` will require linear history) | Site content from #13, #15 and #17 goes live | `deploy-verify / production` runs on push: all pass; `https://usetradelink.com/scripts/build_site.py` returns 404 | Cloudflare → Deployments → previous → Rollback, then revert the PR |
| 4 | Protect `main` | NEEDS CONFIGURATION (admin) | GitHub → Settings → Rules → Rulesets → Import | Import `.github/rulesets/main-protection.json` **from #18** (requires `site-checks` only) | No direct pushes. Nobody can bypass, including you. Merges need green checks and an up-to-date branch | Ruleset shows Active; a test push to `main` is rejected | Set the ruleset Enforcement to Disabled |
| 5 | Merge the automation release #19 | **NEEDS MY APPROVAL** | PR #19 → Edit → base `main` | Retarget, wait for green CI, merge | Nothing public: `automation/`, `docs/`, `.github/` are excluded from `_site` | `deploy-verify / production` stays green | Revert the PR |
| 6 | Require all checks | NEEDS CONFIGURATION | Rulesets → `main-protection` | Re-import from `main`: adds `seo-content-integrity`, `python-tests`, `approval-gateway-tests`, `engine-tests` | Do this only after #19 is merged; before that, these checks never run on `main` PRs and every PR would be blocked | An open PR lists five required checks | Remove the four checks |
| 7 | Railway: deploy only from `main` | NEEDS CONFIGURATION | Railway → each service → Settings → Source | Branch `main`; enable **Wait for CI**; Project Settings → Environments → **PR Environments off** | Pushes to other branches no longer deploy, which is intended | `seo-approvals /health` → `deployed_branch: "main"` | Change the branch back |
| 8 | Confirm existing services' variables | **BLOCKED** (Railway not reachable from the session) | Railway → `seo-auditor`, `seo-approvals` → Variables | Names in `docs/ops/railway.md`. Check `PUBLISHING_ENABLED=false`, `SEMRUSH_ENABLED=false`, Semrush caps unset | — | `/health` → `publishing: armed=false` | — |
| 9 | Search Console data | **BLOCKED** (needs owner confirmation) | Google Search Console → Settings → Users and permissions; Railway `seo-auditor` | Service-account e-mail as a Restricted user; set `GSC_SERVICE_ACCOUNT_JSON` | — | After the 13:00 UTC run: `SELECT max(data_date) FROM seo_search_performance` is recent | Remove the user |
| 10 | Read-only dashboard role | NEEDS CONFIGURATION | Railway Postgres → Query | Run `automation/engine/sql/dashboard_readonly_role.sql` with a new password | — | `SELECT has_table_privilege('seo_dashboard_ro','seo_opportunities','INSERT')` returns false | `DROP ROLE seo_dashboard_ro` |
| 11 | New service `seo-engine` | NEEDS CONFIGURATION (no spend with defaults) | Railway → New → GitHub repo, root `automation/engine`, config `railway.json` | Only `DATABASE_URL`. Leave every `ENGINE_*` / `CONTENT_LLM_*` flag unset | Reads the public site once a day; writes only engine tables | Pre-deploy log: `{"applied": [...]}`; run log: `"semrush": {"enabled": false, "calls": 0}`; `python -m engine.run spend` → caps 0 | Delete the service (tables are additive) |
| 12 | New service `seo-dashboard` | NEEDS CONFIGURATION | Railway → New service, same root, config `railway.dashboard.json` | `DATABASE_URL` (read-only role from step 10), `DASHBOARD_USER`, `DASHBOARD_PASSWORD` (≥16 characters). Generate a domain only if you need browser access | Exposes a login page if a domain is created | `/health` → 200; `/` without credentials → 401 | Remove the domain or the service |
| 13 | Slack (PB Assist) round trip | **BLOCKED** (can't test Slack from the session) | api.slack.com → PB Assist → Interactivity | Request URL `https://<seo-approvals domain>/slack/interactions`; bot in `SLACK_CHANNEL_ID`; approver IDs set | — | After step 3, retarget draft #21 to `main` and add label `seo-agent-proposal`. Discovery only picks up labelled PRs based on `main`; within about 2 minutes a message appears. Click **Reject** or **Request changes**: it is logged, and `/internal/audit` shows it. **Approve** is refused, because the PR is a draft and publishing is disarmed | Remove the label |
| 14 | Review proposals #20, #21, #22 | **NEEDS MY APPROVAL** | GitHub | Retarget each to `main` after #18; review | — | CI green; Cloudflare preview passes `deploy-verify` | Close the PR |
| 15 | Arm publishing | **NEEDS MY APPROVAL** | Railway `seo-approvals` → Variables; GitHub fine-grained token | `GITHUB_TOKEN` with Contents RW + Pull requests RW + Checks R on this repo only; `BRANCH_PROTECTION_VERIFIED=true`; `PUBLISHING_ENABLED=true` | Approved proposal PRs merge to `main` and deploy | `/health` → armed. First Approve: GitHub's squash merge is accepted only when the PR head equals the reviewed SHA (the `sha` parameter); the audit log records both SHAs, and `deploy-verify / production` passes | `PUBLISHING_ENABLED=false`, then restart (the executor re-checks before every merge) |
| 16 | Engine opens its own proposal PRs | **NEEDS MY APPROVAL** | `seo-engine` variables | `ENGINE_PROPOSALS_ENABLED=true`; `ENGINE_GITHUB_TOKEN` (Contents + PR write, cannot merge) | At most 3 open labelled PRs | A new PR is labelled `seo-agent-proposal` | Unset the flag |
| 17 | Semrush spending | **NEEDS MY APPROVAL** (costs units) | `seo-engine` / `seo-auditor` variables | Example first month: `ENGINE_SEMRUSH_ENABLED=true`, `_PER_CALL=120`, `_PER_RUN=300`, `_PER_MONTH=1000` | Spends up to the caps | `engine.run spend` matches the caps; a real cost above the estimate auto-pauses | `SEMRUSH_KILL_SWITCH=true` |
| 18 | Claude drafting of new pages | **NEEDS MY APPROVAL** (billable) | `seo-engine` variables | `CONTENT_LLM_ENABLED=true`, `ANTHROPIC_API_KEY` | Model cost per draft; drafts still pass the quality gate and need Slack approval | `engine_api_usage` rows | Unset the flag |
| 19 | Product-claim decisions | **NEEDS MY APPROVAL** | `automation/engine/facts/tradelink_facts.json` | Decide on NinjaTrader (homepage FAQ) and "ten protection rules" (homepage, `llms.txt`, TradeSyncer comparison) | — | `engine.run audit-content` has no `disputed`/`needs_confirmation` errors | Revert the facts change |
