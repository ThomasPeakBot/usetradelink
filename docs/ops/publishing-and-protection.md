# Publishing, branch protection and deployment

`docs/` is excluded from the public build (`scripts/build_site.py`).

## 1. Stop publishing the repository root (do this before merging any automation PR)
Cloudflare Pages (project `usetradelink`) builds every branch from the repository root, so
`automation/**`, `docs/**`, `.github/**` and tests become public URLs on production and on
branch preview URLs.

Cloudflare dashboard → Workers & Pages → `usetradelink` → Settings → Builds:
- Build command: `python3 scripts/build_site.py`
- Build output directory: `_site`

Then trigger a preview deploy of this branch and confirm, on the preview URL:
- `/` and every sitemap URL return 200
- `/scripts/build_site.py`, `/docs/ops/publishing-and-protection.md` and `/.github/workflows/site-checks.yml` return 404

Also review Settings → Builds → Branch control: limit preview deployments to "None" or to
specific branches if branch previews should not be public.

## 2. Protect `main`
Settings → Rules → Rulesets → New → Import, and choose `.github/rulesets/main-protection.json`.
It requires a pull request, the `site-checks` status, linear history, resolved threads, and blocks
force-pushes and deletion, with **no bypass actors**.
After PR #9/#11 merges, edit the ruleset to also require `seo-content-integrity`.
The approval executor checks this at runtime (`branch_protection_ok`) and refuses to merge without it.

## 3. Optional: approval-gated deploys from GitHub instead of Cloudflare Git integration
`.github/workflows/deploy-site.yml` deploys `_site` with Wrangler through the `production`
environment. To use it:
1. Settings → Environments → `production` → Required reviewers: add the owner; deployment branches: `main` only.
2. Add secrets `CLOUDFLARE_API_TOKEN` (Pages:Edit for this project only) and `CLOUDFLARE_ACCOUNT_ID`.
3. Disable automatic production deployments in Cloudflare's Git integration.
4. Set the repository variable `SITE_DEPLOY_ENABLED=true`.
Until step 4 the workflow is skipped and changes nothing.

## 4. Rollback
Cloudflare Pages → Deployments → choose the previous production deployment → "Rollback to this
deployment". Then revert the merge commit on `main` in a PR so the repository matches production.
