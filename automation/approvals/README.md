# TradeLink SEO — Phase 2 Slack approvals

This service is intentionally **disarmed until the user finishes Slack and GitHub setup**.
The Phase 1 \`seo-auditor\` remains a separate, read-only Railway cron service.

## Architecture and actions
- POST /internal/proposals: authenticated agent registers an existing GitHub PR and posts a Slack message containing its exact SHA, summary, review link and buttons.
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

## Slack app setup (done by workspace administrator)
1. Create an app **from scratch** at https://api.slack.com/apps, e.g. \`TradeLink SEO Agent\`.
2. Under OAuth & Permissions, request bot scopes \`chat:write\` (for notifications). Install app to your Slack workspace and invite it to a private \`#tradelink-seo-approvals\` channel.
3. Under Interactivity & Shortcuts, enable interactivity, Request URL:
   \`https://YOUR-RAILWAY-DOMAIN/slack/interactions\`
4. Copy the app's **Signing Secret** and **Bot User OAuth Token** into Railway *private service variables*, not in GitHub or ChatGPT.
5. Get actual Slack **channel ID**, **workspace/team ID**, and **approver member user IDs** from Slack. Do not use a username or display name as an authorization identity.
6. Add variables to Railway \`seo-approvals\`, keeping PUBLISHING_ENABLED=false until all checks below pass.

Required vars:
- DATABASE_URL = \${{Postgres.DATABASE_URL}} (Railway reference)
- SLACK_SIGNING_SECRET = secret from app
- SLACK_BOT_TOKEN = xoxb-...
- SLACK_CHANNEL_ID = C... (private channel sometimes G...)
- SLACK_TEAM_ID = T...
- SLACK_APPROVER_IDS = U123,U456 (comma-separated)
- PROPOSAL_API_TOKEN = high-entropy private token for the drafting agent
- GITHUB_TOKEN = dedicated repo-scoped permission to read PRs and merge approved PRs
- PUBLISHING_ENABLED = false (default)
- BRANCH_PROTECTION_VERIFIED = false (default)

GitHub token can be added before publishing is armed, but should not be enabled until branch protection and authenticated end-to-end Slack test pass. Never reuse the Google service account JSON. The Slack service does **not** need GSC credentials. The GSC collector keeps its separate read-only scope.

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
At construction time no Slack app credentials, GitHub publishing token, or allowlist were provided. A live service can be deployed safely and respond to health checks but must remain unable to approve/publish until the Slack administrator connects it. This is a **deliberate** permission barrier, not a broken deployment.

## Local test
\`python -m pip install -r requirements.txt\`
\`python -m unittest discover -s tests -v\`
\`uvicorn app:app --host 0.0.0.0 --port 8080\`
