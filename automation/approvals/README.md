# TradeLink SEO — Phase 2 Slack approvals

This service is intentionally **disarmed until the user finishes Slack and GitHub setup**.
The Phase 1 \`seo-auditor\` remains a separate, read-only Railway cron service.

## Architecture and actions
- **Automatic discovery:** every 120 seconds the gateway checks GitHub for PRs targeting main with label `seo-agent-proposal`. It posts each new exact-SHA proposal to Slack once the Slack app and GitHub access are configured. New commits on a pending proposal supersede the old one; every new SHA requires a new decision.
- POST /internal/proposals: an authenticated drafting agent can also register an existing GitHub PR directly and post its exact SHA, summary, review link and buttons.
- POST /slack/interactions: verifies Slack request HMAC over raw bytes, rejects stale requests, checks workspace + approver allowlist.
- Approve: records identity and exact SHA; queues asynchronous execution. It does NOT claim success before GitHub returns a successful merge.
- Reject: marks the proposal rejected, cancels unstarted queued work.
- Request changes: opens a Slack modal, stores the user's instructions, posts a GitHub PR comment and stops publication. The drafting agent must revise the PR and submit a **new** proposal at the new SHA.
- GET /internal/audit: last 100 append-only approval events; POST/GET internal endpoints require PROPOSAL_API_TOKEN.
- GET /internal/opportunities: early read-only GSC queries for content optimization candidates.
- GET /health: configuration health indicators only. No secrets or private operations.

## Permissions / fail-closed policy
- Publish is OFF until \`PUBLISHING_ENABLED=true\` AND \`BRANCH_PROTECTION_VERIFIED=true\` AND all required credentials exist.
- Restrict SLACK_APPROVER_IDS and SLACK_TEAM_ID. A channel member who is not allowlisted cannot approve.
- Approve only an exact PR commit SHA. If the PR changes, executor refuses it and a new proposal is required.
- GitHub PR must be open, same repo, target main, non-draft, mergeable, with successful **seo-content-integrity** GitHub check and no failing check runs.
- Only content HTML paths under selected SEO guide directories, sitemap.xml and llms.txt are eligible. No JS/CSS, checkout, brand assets, homepage, workflow files, automation code, file deletions or renames.
- All other site changes must take the manual separate approval path until explicitly designed and tested.
- Auto-merge is not enabled. The dedicated executor calls GitHub merge *after* the user's explicit approval.
- Github merge is not proof Cloudflare has deployed. Service reports \`merged_verification_pending\` until production deployment can be independently confirmed.
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
Database transactions and FOR UPDATE SKIP LOCKED prevent simultaneous claims. A crash after a job claim leaves it in an attention-required state until manually reconciled (fail closed, not blind retry). Logs never show Slack/GitHub token values.

## Current deployment state
At construction time no Slack app credentials, GitHub publishing token, or Slack approver allowlist were provided. A live service can be deployed safely and respond to health checks but must remain unable to approve/publish until the Slack administrator connects it. This is a **deliberate** permission barrier, not a broken deployment.

## Local test
\`python -m pip install -r requirements.txt\`
\`python -m unittest discover -s tests -v\`
\`uvicorn app:app --host 0.0.0.0 --port 8080\`

## Deployment URLs
- Railway project: https://railway.com/project/ea11f4bd-6944-490a-a1c4-4c6a9cbfe131
- Slack callback: https://seo-approvals-production.up.railway.app/slack/interactions
- Health: https://seo-approvals-production.up.railway.app/health
- App manifest: https://github.com/ThomasPeakBot/usetradelink/blob/seo/phase2-slack-approvals-20261016/automation/approvals/slack-app-manifest.yaml
