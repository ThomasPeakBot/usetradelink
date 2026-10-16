# TradeLink SEO — Phase 2 Slack approvals

This service is intentionally **disarmed until the user finishes Slack and GitHub setup**.
The Phase 1 \`seo-auditor\` remains a separate, read-only Railway cron service.

## Architecture and actions
- **Automatic discovery:** every 120 seconds the gateway checks GitHub for PRs targeting main with label `seo-agent-proposal`. It posts each new exact-SHA proposal to Slack once the Slack app and GitHub access are configured. New commits on a pending proposal supersede the old one; every new SHA requires a new decision.
- POST /internal/proposals: an authenticated drafting agent can also register an existing GitHub PR directly and post its exact SHA, summary, review link and buttons.
- POST /slack/interactions: verifies Slack request HMAC over raw bytes, rejects stale requests, stores a digest of each signature to refuse replays, checks workspace + approver allowlist and that button presses come from \`SLACK_CHANNEL_ID\`. Interactions whose action/callback IDs are not \`seo_*\` get an empty 200 so another subsystem sharing the Slack app is not answered with SEO errors. User feedback goes to Slack's \`response_url\` (Slack ignores the HTTP body of button presses).
- Approve: records identity and exact SHA; queues asynchronous execution. It does NOT claim success before GitHub returns a successful merge.
- Reject: marks the proposal rejected, cancels unstarted queued work.
- Request changes: opens a Slack modal, stores the user's instructions, posts a GitHub PR comment and stops publication. The drafting agent must revise the PR and submit a **new** proposal at the new SHA.
- GET /internal/audit: last 100 append-only approval events; POST/GET internal endpoints require PROPOSAL_API_TOKEN.
- GET /internal/opportunities: early read-only GSC queries for content optimization candidates.
- GET /health: configuration health indicators only. No secrets or private operations.

## Permissions / fail-closed policy
- Publish is OFF until \`PUBLISHING_ENABLED=true\` AND \`BRANCH_PROTECTION_VERIFIED=true\` AND all required credentials exist (including \`SLACK_CHANNEL_ID\`).
- \`BRANCH_PROTECTION_VERIFIED\` is only an operator acknowledgement. Before every merge the executor asks GitHub whether \`main\` is protected and refuses unless it can confirm either (a) classic protection with a required pull request, no bypass allowances, required \`seo-content-integrity\` status, enforced for admins, no force pushes or deletions (needs **Administration: read** on the token), or (b) an active ruleset with pull_request, required status check \`seo-content-integrity\`, non_fast_forward and deletion rules that the executor's own token cannot bypass (\`current_user_can_bypass = never\`). Any lookup failure keeps publishing blocked.
- Only PRs carrying the \`seo-agent-proposal\` label can be published, and the required check must be created by GitHub Actions (a check run with the same name from another app is ignored). Every other check run and commit status on the SHA must be complete and passing.
- Restrict SLACK_APPROVER_IDS and SLACK_TEAM_ID. A channel member who is not allowlisted cannot approve.
- Approve only an exact PR commit SHA. If the PR changes, executor refuses it and a new proposal is required.
- GitHub PR must be open, same repo, target main, non-draft, mergeable, with successful **seo-content-integrity** GitHub check and no failing check runs.
- Only content HTML paths under selected SEO guide directories, sitemap.xml and llms.txt are eligible. No JS/CSS, checkout, brand assets, homepage, workflow files, automation code, file deletions or renames.
- All other site changes must take the manual separate approval path until explicitly designed and tested.
- Auto-merge is not enabled. The dedicated executor calls GitHub merge *after* the user's explicit approval.
- Github merge is not proof Cloudflare has deployed. The executor fetches each merged file with a cache-busting query and compares it to the merged source after removing Cloudflare-injected scripts/attributes, comments and whitespace differences. Anything else reports \`merged_verification_pending\`.
- The \`seo-content-integrity\` workflow also runs \`content_guard.py\`: allowlisted pages may only load \`/tracking.js\`, \`/seo.css\`, JSON-LD and the GTM noscript iframe; inline scripts, event handlers, forms, other iframes, meta refresh, javascript: URLs, noindex and any change to the checkout URL fail the check.
- Hard requirement: enable GitHub branch protection on \`main\` before turning on publishing. Require pull requests and successful \`seo-content-integrity\`; limit bypass and direct pushes.
- Do not create any Slack approval workflow with an unrestricted repository credential. Prefer a dedicated GitHub App installation token with only repo Content/Pull requests writes.

## Slack integration — REUSE the existing EmailCreator Slack app (preferred)

**No second Slack app is necessary.** EmailCreator already receives Slack message events on its `/slack/events` route. Keep the existing Slack **Event Subscriptions Request URL** exactly as configured. Event Subscriptions and Interactivity & Shortcuts are separate app settings.

1. Open the *existing* EmailCreator Slack app at https://api.slack.com/apps. Do **not** create another app and do **not** import the new-app manifest into the existing one.
2. Open **Interactivity & Shortcuts**. If it is disabled or its Request URL is blank, enable it and set its Request URL to:
   `https://seo-approvals-production.up.railway.app/slack/interactions`
3. **If Interactivity & Shortcuts already has a URL, do not overwrite it.** Slack supports one interactivity callback per app: inspect existing consumers, then place a verified Slack interaction router in front of both destinations, dispatching `seo_approve`, `seo_reject`, `seo_revise`, and `seo_revision_modal` to the SEO gateway while keeping old actions on their old handler. Do not cut over without tests for both services.
4. Verify the bot has the `chat:write` OAuth scope. Invite the **existing** Slack bot to a private `#tradelink-seo-approvals` channel.
5. In the existing app's **Basic Information**, locate its signing secret. In **OAuth & Permissions**, locate its current bot token. Store these values *only* in Railway service variables for `seo-approvals`, never in this repo or chat. Do not rotate the existing secrets as part of this setup; rotation could break EmailCreator.
6. Obtain actual Slack channel ID, workspace/team ID and authorized approver user IDs. Make the authorizer allowlist as narrow as necessary (user IDs, not display names).
7. Keep publishing disarmed until a signed interactive callback has been successfully tested, EmailCreator is verified unaffected, and protected-main + GitHub app controls have been configured.

Required variables on `seo-approvals`:
- `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (Railway reference; already set)
- `SLACK_SIGNING_SECRET` = existing app's Signing Secret
- `SLACK_BOT_TOKEN` = existing app's Bot User OAuth Token
- `SLACK_CHANNEL_ID` = private approvals channel ID
- `SLACK_TEAM_ID` = workspace ID
- `SLACK_APPROVER_IDS` = comma-separated Slack member IDs
- `PROPOSAL_API_TOKEN` = stored privately already in Railway
- `GITHUB_TOKEN` = separate, repo-scoped GitHub token for proposed PRs and approved merges
- `PUBLISHING_ENABLED` = `false` until verified
- `BRANCH_PROTECTION_VERIFIED` = `false` until verified

One shared Slack app may post to multiple channels, but each subsystem keeps its own database and authorization checks. Do not replace EmailCreator's existing Events API URL, credentials or message handlers. The Slack service does **not** need GSC credentials; the SEO crawler remains read-only.

For a completely new Slack workspace *without* a reusable app, `slack-app-manifest.yaml` is available as a fallback only, not the recommended integration.

## End-to-end acceptance test
- /health shows Slack configured, DB configured, and publishing_armed false.
- Invalid Slack HMAC, stale timestamps, outsiders and non-allowlisted channel members cannot authorize any changes.
- Create a harmless **content-only** PR targeting main, wait until seo-content-integrity succeeds.
- POST authenticated registration to /internal/proposals with JSON { "pr_number": ..., "summary": "..."}; bot posts Slack buttons.
- Reject test => DB event exists; PR remains unmerged.
- Revision test => modal accepts text; DB event + PR comment; PR remains unmerged.
- Approval test with publishing disabled => response says disarmed and PR remains unmerged.
- Protect main; verify checks; enable executor. Approve a new test PR SHA; verify GitHub returns merged, then independently check Cloudflare production before declaring live.
- Restore disabled flag if credentials are invalid or a check is missing.

## Worker
The HTTP process starts a single lightweight job polling thread **only when armed**.
Database transactions and FOR UPDATE SKIP LOCKED prevent simultaneous claims. A job still marked running after 15 minutes (for example a crash mid-merge) is moved to \`needs_attention\` and never retried automatically; check GitHub before re-approving. Logs never show Slack/GitHub token values.

## Current deployment state
At construction time no Slack app credentials, GitHub publishing token, or Slack approver allowlist were provided. A live service can be deployed safely and respond to health checks but must remain unable to approve/publish until the Slack administrator connects it. This is a **deliberate** permission barrier, not a broken deployment.

## Local test
\`python -m pip install -r requirements.txt\`
\`python -m unittest discover -s tests -v\` (set \`TEST_DATABASE_URL\` to a disposable Postgres to include the database flow tests)
\`uvicorn app:app --host 0.0.0.0 --port 8080\`

## Deployment URLs
- Railway project: https://railway.com/project/ea11f4bd-6944-490a-a1c4-4c6a9cbfe131
- Slack callback: https://seo-approvals-production.up.railway.app/slack/interactions
- Health: https://seo-approvals-production.up.railway.app/health
- App manifest: https://github.com/ThomasPeakBot/usetradelink/blob/seo/phase2-slack-approvals-20261016/automation/approvals/slack-app-manifest.yaml

## Railway deployment source (required before arming)
Railway services currently build from development branches. Code cannot change a Railway service's connected branch, so the service enforces it at runtime instead:
- `armed()` is false unless `RAILWAY_GIT_BRANCH` (set by Railway) is in `PUBLISH_ALLOWED_BRANCHES` (default `main`). A deploy from `seo/phase2-...` or any feature branch can never publish, whatever its variables say.
- `/health` reports `deployed_branch`, `deployed_commit` and `deployed_from_allowed_branch`, so the running SHA can be compared with the reviewed one.
- `railway.json` sets `watchPatterns` to `/automation/approvals/**`, so website or other service changes do not redeploy this token-bearing service.

Operator steps (Railway dashboard → service → Settings → Source):
1. After the reviewed PRs merge, change the connected branch of `seo-approvals` and `seo-auditor` from the development branches to `main`.
2. Turn on **Wait for CI** so a deploy only happens after GitHub checks pass.
3. Until then, keep `PUBLISHING_ENABLED=false` and do not give the service a GitHub token with merge rights.
