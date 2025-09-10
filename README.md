# scrapy

Confluence → GitHub Pages (POC). This syncs a Confluence page tree into a static site and publishes it to GitHub Pages using MkDocs and GitHub Actions.

## How it works
- A Python script calls the Confluence Cloud REST API to fetch a root page and its descendants.
- It rewrites image links to local assets and converts Confluence HTML to Markdown.
- It generates a navigable MkDocs site and deploys it to the `gh-pages` branch.

## Local quickstart
1. Python 3.9+ recommended. Then:
   - `python3 -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt`

2. Set env vars (Confluence Cloud):
   - `CONFLUENCE_BASE_URL` (e.g. `https://YOUR-SITE.atlassian.net/wiki`)
   - `CONFLUENCE_EMAIL` (your Atlassian account email)
   - `CONFLUENCE_API_TOKEN` (create at https://id.atlassian.com/manage-profile/security/api-tokens)
   - `CONFLUENCE_ROOT_PAGE_ID` (numeric ID of the root page)

3. Run sync:
   - `python scripts/sync_confluence.py`
   - Built site locally: `mkdocs serve` and open http://127.0.0.1:8000

## GitHub Actions (CI/CD)
This repo contains `.github/workflows/sync-and-deploy.yml` which:
- Runs on schedule and on demand.
- Installs dependencies.
- Syncs docs from Confluence.
- Builds MkDocs and deploys to `gh-pages` using the built-in `GITHUB_TOKEN`.

### Required repository secrets
Create these in your GitHub repo Settings → Secrets and variables → Actions:
- `CONFLUENCE_BASE_URL` (e.g. `https://mellodoes.atlassian.net/wiki`)
- `CONFLUENCE_EMAIL`
- `CONFLUENCE_API_TOKEN`
- `CONFLUENCE_ROOT_PAGE_ID`

> If you want a custom domain, add a `CNAME` file under `docs/` or set `cname` in the deploy step here.

## Notes / Limitations (POC)
- Converts content via `markdownify`; some complex Confluence macros may render as plain HTML.
- Images/attachments referenced in pages are downloaded to `docs/assets/<page_id>/...`.
- Internal page links are rewritten when the target is part of the exported tree.
- Nav mirrors the Confluence hierarchy from the chosen root.

## Repo structure
- `scripts/sync_confluence.py` — scraper + converter
- `docs/` — generated content (safe to commit)
- `mkdocs.yml` — auto-updated with the nav by the script
- `.github/workflows/sync-and-deploy.yml` — CI pipeline for Pages
