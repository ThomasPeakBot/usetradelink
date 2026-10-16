# Railway: production configuration for the SEO services

Project `tradelink-seo-agent` (ID `ea11f4bd-6944-490a-a1c4-4c6a9cbfe131`). Every service builds from this
repository with **Source branch `main`** and **Wait for CI** on. Config-as-code lives next to each service.

| Service | Root directory | Config file | Type | Schedule / health |
|---|---|---|---|---|
| `seo-auditor` (exists) | `automation/seo` | `railway.json` | Cron | `0 13 * * *` UTC |
| `seo-approvals` (exists) | `automation/approvals` | `railway.json` | Web (private Slack callback) | `/health` |
| `seo-engine` (new) | `automation/engine` | `railway.json` | Cron | `30 14 * * *` UTC; pre-deploy `python -m engine.run migrate` |
| `seo-dashboard` (new) | `automation/engine` | `railway.dashboard.json` | Web (internal) | `/health` |
| `Postgres` (exists) | — | — | Database | Railway backups on |

`watchPatterns` in each config limit redeploys to that service's own folder.

## Variables (names only; set values in Railway, never in the repo)
| Service | Required | Optional / spending (all off by default) |
|---|---|---|
| seo-auditor | `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `GSC_SERVICE_ACCOUNT_JSON` | `SEMRUSH_ENABLED=false`, `SEMRUSH_API_KEY`, `SEMRUSH_MAX_UNITS_PER_RUN`, `SEMRUSH_MAX_UNITS_PER_MONTH` (unset = 0 = no spending), `SEMRUSH_KILL_SWITCH` |
| seo-approvals | `DATABASE_URL`, `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`, `SLACK_TEAM_ID`, `SLACK_APPROVER_IDS`, `PROPOSAL_API_TOKEN` | `GITHUB_TOKEN` (read-only until publishing is approved), `PUBLISHING_ENABLED=false`, `BRANCH_PROTECTION_VERIFIED=false`, `PUBLISH_ALLOWED_BRANCHES=main` |
| seo-engine | `DATABASE_URL` | `ENGINE_SEMRUSH_ENABLED=false` + `SEMRUSH_API_KEY` + `ENGINE_SEMRUSH_MAX_UNITS_PER_RUN/_PER_MONTH/_PER_CALL`, `SEMRUSH_KILL_SWITCH`, `ENGINE_COMPETITOR_CRAWL=false`, `CONTENT_LLM_ENABLED=false` + `ANTHROPIC_API_KEY`, `ENGINE_PROPOSALS_ENABLED=false` + `ENGINE_GITHUB_TOKEN` |
| seo-dashboard | `DATABASE_URL` (read-only role, see `automation/engine/sql/dashboard_readonly_role.sql`), `DASHBOARD_USER`, `DASHBOARD_PASSWORD` (≥16 chars) | — |

## Why development branches can no longer publish
- Each service's Source branch is `main` (setting above). `main` is protected by `.github/rulesets/main-protection.json`.
- In code: `seo-approvals` refuses to arm publishing unless `RAILWAY_GIT_BRANCH` is in `PUBLISH_ALLOWED_BRANCHES` (default `main`).
  `/health` reports `deployed_branch` and `deployed_commit`.
- Railway PR environments: keep **PR Environments off** for this project (Project Settings → Environments), so PR branches never get
  deployments that hold production variables.

## Database migrations
- Collector and approval tables are created idempotently by those services at start/run (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
- Engine tables use versioned migrations (`automation/engine/migrations/*.sql`, recorded in `engine_schema_migrations`), applied under an
  advisory lock by the pre-deploy command and at the start of each run.
- The dashboard never writes; it tolerates missing tables.

## Rollback
- Railway → service → Deployments → previous deployment → **Redeploy**. Migrations are additive (no drops), so older code keeps working.
- Emergency stop of all Semrush spending without a deploy: set `SEMRUSH_KILL_SWITCH=true` on `seo-auditor` and `seo-engine`, or
  `python -m engine.run spend --pause "reason"` (or `UPDATE seo_spend_controls SET paused=true WHERE provider='semrush'`).
- Emergency stop of publishing: set `PUBLISHING_ENABLED=false` on `seo-approvals` (takes effect on restart; executor also re-checks before every merge).
