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

2. Add a `.env` file in the repo root (auto-loaded):
   - `CONFLUENCE_BASE_URL=https://YOUR-SITE.atlassian.net/wiki`
   - `CONFLUENCE_EMAIL=you@example.com`
   - `CONFLUENCE_API_TOKEN=...` (create at https://id.atlassian.com/manage-profile/security/api-tokens)
   - `CONFLUENCE_ROOT_PAGE_ID=123456`

3. Run sync (vSept15 v2):
   - `python3 scripts/sync_confluence_vSept15_v2.py`
   - This builds to a temporary folder and deploys to `gh-pages` directly. No local `site/` or `.generated_docs/` remains after completion.

4. Optional local preview (legacy approach):
   - If you use older scripts that write to `docs/` or `site/`, you can preview with `mkdocs serve` and open http://127.0.0.1:8000

### Zero local artifacts
- `site/` and `.generated_docs/` are ignored and cleaned automatically by the vSept15 v2 script.
- Nothing generated is committed to `main`; only the `gh-pages` branch is updated during deploy.
- This makes it safe for others to clone the repo, set their `.env`, and run the sync without polluting their working tree.

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
- `scripts/sync_confluence.py` — scraper + converter (modular version, recommended)
- `scripts/sync_confluence_vSept15_v2.py` — latest version used for CI-like local deploys; builds in a temp dir and cleans up
- `scripts/sync_confluence_vSep10.py` — previous monolithic implementation (kept for reference)
- `docs/` — generated content (safe to commit)
- `mkdocs.yml` — auto-updated with the nav by the script
- `.github/workflows/sync-and-deploy.yml` — CI pipeline for Pages

## Script versions
- New (recommended): `scripts/sync_confluence.py`
  - Modular design with `Config`, `ConfluenceClient`, `PageProcessor`, `MkDocsWriter`
  - Better logging, retries, asset dedup, and safer path handling (pathlib)
  - Preserves your existing `mkdocs.yml` and only updates `nav`
  - Run: `python scripts/sync_confluence.py`

- Legacy (Sep 10): `scripts/sync_confluence_vSep10.py`
  - Single‑file procedural flow used in the initial POC
  - Run: `python scripts/sync_confluence_vSep10.py`

## Prompts
- `Codex_Prompts_vSept11.txt` — prompt used to generate the new modular version.
- `Codex_Prompts.txt` — prompt used for the initial POC version.

## Usage
- Local sync (latest): `python3 scripts/sync_confluence_vSept15_v2.py`
- Local sync (recommended modular alt): `python3 scripts/sync_confluence.py`
- Local sync (legacy): `python3 scripts/sync_confluence_vSep10.py`
- Preview locally: `mkdocs serve` then open `http://127.0.0.1:8000`
- Manual deploy: `gh workflow run .github/workflows/sync-and-deploy.yml --ref main`
- Watch run: `gh run list --limit 1 && gh run watch <RUN_ID>`
- Live site: https://oyemello.github.io/scrapy/
