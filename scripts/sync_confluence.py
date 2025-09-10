#!/usr/bin/env python3
import os
import re
import sys
import json
import time
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from dotenv import load_dotenv
import yaml


def env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val


def slugify(value: str) -> str:
    value = value.strip().lower()
    # Replace slashes and spaces with dash
    value = re.sub(r"[\s/]+", "-", value)
    # Remove anything not alphanum, dash or underscore
    value = re.sub(r"[^a-z0-9\-_]", "", value)
    value = re.sub(r"-+", "-", value)
    return value or "page"


@dataclass
class Page:
    id: str
    title: str
    ancestors: List[Dict]
    html: str
    children: List[str] = field(default_factory=list)


class ConfluenceClient:
    def __init__(self, base_url: str, email: str, token: str):
        # base_url should be like https://<site>.atlassian.net/wiki
        self.base_url = base_url.rstrip("/")
        self.api = f"{self.base_url}/rest/api"
        self.session = requests.Session()
        self.session.auth = (email, token)
        self.session.headers.update({
            "Accept": "application/json"
        })

    def _get(self, path: str, params: Optional[Dict] = None) -> dict:
        url = path if path.startswith("http") else f"{self.api}{path}"
        while True:
            r = self.session.get(url, params=params)
            if r.status_code == 429:
                retry = int(r.headers.get("Retry-After", "1"))
                time.sleep(retry)
                continue
            r.raise_for_status()
            return r.json()

    def _get_raw(self, url: str) -> requests.Response:
        r = self.session.get(url, stream=True)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "1")))
            return self._get_raw(url)
        r.raise_for_status()
        return r

    def get_page(self, page_id: str) -> Page:
        data = self._get(f"/content/{page_id}", params={
            "expand": "body.view,ancestors"
        })
        return Page(
            id=str(data["id"]),
            title=data.get("title", f"Page {page_id}"),
            ancestors=[{"id": str(a["id"]), "title": a.get("title", str(a["id"]))} for a in data.get("ancestors", [])],
            html=data.get("body", {}).get("view", {}).get("value", ""),
        )

    def list_children(self, page_id: str) -> List[Page]:
        pages: List[Page] = []
        start = 0
        limit = 100
        while True:
            data = self._get(f"/content/{page_id}/child/page", params={
                "limit": limit,
                "start": start,
                "expand": "body.view,ancestors"
            })
            for item in data.get("results", []):
                pages.append(Page(
                    id=str(item["id"]),
                    title=item.get("title", str(item["id"])) ,
                    ancestors=[{"id": str(a["id"]), "title": a.get("title", str(a["id"]))} for a in item.get("ancestors", [])],
                    html=item.get("body", {}).get("view", {}).get("value", ""),
                ))
            if data.get("_links", {}).get("next"):
                start += limit
            else:
                break
        return pages

    def download(self, url_or_path: str, dest_path: str) -> None:
        url = url_or_path
        if url.startswith("/"):
            # relative to /wiki
            url = f"{self.base_url}{url}"
        # ensure directory exists
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with self._get_raw(url) as r:
            with open(dest_path, "wb") as f:
                shutil.copyfileobj(r.raw, f)


def collect_tree(client: ConfluenceClient, root_id: str) -> Dict[str, Page]:
    pages: Dict[str, Page] = {}

    def dfs(page_id: str):
        if page_id in pages:
            return
        page = client.get_page(page_id)
        pages[page_id] = page
        children = client.list_children(page_id)
        page.children = [p.id for p in children]
        for child in children:
            pages[child.id] = child
            dfs(child.id)

    dfs(root_id)
    return pages


def build_paths(pages: Dict[str, Page], root_id: str, docs_dir: str) -> Dict[str, str]:
    # id -> relative output path (from docs_dir)
    file_map: Dict[str, str] = {}

    for pid, page in pages.items():
        # compute ancestor segments starting after root
        segments: List[str] = []
        # Confluence ancestors are ordered oldest->parent
        after_root = False
        for anc in page.ancestors:
            if str(anc["id"]) == str(root_id):
                after_root = True
                continue
            if after_root:
                segments.append(slugify(anc["title"]))

        # filename
        if pid == str(root_id):
            filename = "index.md"
        else:
            filename = f"{slugify(page.title)}-{pid}.md"

        rel_path = os.path.join(*segments, filename) if segments else filename
        file_map[pid] = rel_path

    # Ensure uniqueness (append id already ensures uniqueness)
    return file_map


def rewrite_html(
    client: ConfluenceClient,
    page: Page,
    html: str,
    page_rel_path: str,
    file_map: Dict[str, str],
    docs_dir: str,
) -> Tuple[str, List[str]]:
    """Rewrite <img> and <a> links. Download images locally.
    Returns (rewritten_html, downloaded_files)
    """
    soup = BeautifulSoup(html, "html.parser")
    downloaded: List[str] = []

    # Compute folder of the page
    page_dir = os.path.dirname(page_rel_path)

    # Images
    for img in soup.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        # Normalize to absolute wiki path if needed for checks
        is_confluence = False
        if src.startswith("/wiki/") or src.startswith("/download/"):
            is_confluence = True
        elif "atlassian.net/wiki" in src:
            is_confluence = True

        if is_confluence:
            # compute filename
            clean = src
            # strip query string
            clean = clean.split("?", 1)[0]
            filename = os.path.basename(clean)
            asset_rel_dir = os.path.join("assets", page.id)
            asset_rel_path = os.path.join(asset_rel_dir, filename)
            asset_abs_path = os.path.join(docs_dir, asset_rel_path)

            try:
                # Normalize to an absolute path if not a full URL
                normalized_src = src
                if not src.startswith("http"):
                    normalized_src = "/" + src.lstrip("/")
                client.download(normalized_src, asset_abs_path)
                downloaded.append(asset_rel_path)
                # rewrite src to relative path from the page location
                page_folder_abs = os.path.join(docs_dir, page_dir) if page_dir else docs_dir
                new_src = os.path.relpath(os.path.join(docs_dir, asset_rel_path), start=page_folder_abs)
                img["src"] = new_src
            except Exception as e:
                print(f"Warn: failed to download image {src} for page {page.id}: {e}")

    # Links to other pages
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        # Skip anchors and external links
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("http") and "atlassian.net/wiki" not in href:
            continue
        # Try to extract page id from common Confluence URL patterns
        m = re.search(r"/pages/(\d+)", href)
        if not m:
            # Some URLs look like /wiki/spaces/KEY/pages/<id>/Title
            m = re.search(r"/spaces/[A-Z0-9\-_]+/pages/(\d+)", href)
        if not m:
            continue
        target_id = m.group(1)
        if target_id in file_map:
            target_rel = file_map[target_id]
            # preserve anchor if present
            anchor = ""
            if "#" in href:
                anchor = "#" + href.split("#", 1)[1]
            page_folder_abs = os.path.join(docs_dir, page_dir) if page_dir else docs_dir
            new_href = os.path.relpath(os.path.join(docs_dir, target_rel), start=page_folder_abs) + anchor
            a["href"] = new_href

    return str(soup), downloaded


def html_to_markdown(html: str) -> str:
    # markdownify is imperfect; allow raw HTML for safety by preserving tables/lines
    return md(html, heading_style="ATX", strip=None)


def write_pages(
    client: ConfluenceClient,
    pages: Dict[str, Page],
    file_map: Dict[str, str],
    docs_dir: str,
    root_id: str,
) -> None:
    # Ensure clean docs dir exists
    os.makedirs(docs_dir, exist_ok=True)

    for pid, page in pages.items():
        rel_path = file_map[pid]
        abs_path = os.path.join(docs_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        rewritten_html, _ = rewrite_html(
            client=client,
            page=page,
            html=page.html,
            page_rel_path=rel_path,
            file_map=file_map,
            docs_dir=docs_dir,
        )
        md_content = html_to_markdown(rewritten_html)
        # Root page becomes index.md; ensure a top header exists
        if pid == str(root_id) and not re.search(r"^# ", md_content, re.MULTILINE):
            md_content = f"# {page.title}\n\n" + md_content

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(md_content)


def build_nav(pages: Dict[str, Page], file_map: Dict[str, str], root_id: str) -> List:
    # Build a tree structure for nav
    children_map: Dict[str, List[str]] = {pid: p.children for pid, p in pages.items()}

    def node_to_nav(pid: str) -> Dict:
        page = pages[pid]
        title = page.title
        rel = file_map[pid]
        return {title: rel}

    def recurse(pid: str) -> List:
        items: List = []
        for child_id in children_map.get(pid, []):
            child = pages[child_id]
            if child.children:
                # Section with the page itself followed by its children
                section_items = [node_to_nav(child_id)]
                section_items.extend(recurse(child_id))
                items.append({child.title: section_items})
            else:
                items.append(node_to_nav(child_id))
        return items

    # Root is Home (index.md)
    nav: List = [{"Home": file_map[root_id]}]
    nav.extend(recurse(root_id))
    return nav


def update_mkdocs_yaml(site_name: str, nav: List, mkdocs_path: str = "mkdocs.yml") -> None:
    base = {
        "site_name": site_name,
        "theme": {"name": "mkdocs"},
        "docs_dir": "docs",
    }
    if os.path.exists(mkdocs_path):
        try:
            with open(mkdocs_path, "r", encoding="utf-8") as f:
                base = yaml.safe_load(f) or base
        except Exception:
            pass
    base["nav"] = nav
    with open(mkdocs_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(base, f, sort_keys=False, allow_unicode=True)


def main() -> None:
    # Load .env if present; do not override already-set environment variables
    load_dotenv(override=False)

    base_url = env("CONFLUENCE_BASE_URL")
    email = env("CONFLUENCE_EMAIL")
    token = env("CONFLUENCE_API_TOKEN")
    root_id = env("CONFLUENCE_ROOT_PAGE_ID")

    docs_dir = os.path.join(os.getcwd(), "docs")
    os.makedirs(docs_dir, exist_ok=True)

    client = ConfluenceClient(base_url, email, token)
    print(f"Collecting pages from root {root_id}...")
    pages = collect_tree(client, root_id)
    print(f"Collected {len(pages)} pages")

    file_map = build_paths(pages, root_id, docs_dir)
    write_pages(client, pages, file_map, docs_dir, root_id)
    nav = build_nav(pages, file_map, root_id)
    site_name = pages[root_id].title if root_id in pages else "Confluence Docs"
    update_mkdocs_yaml(site_name, nav)
    print("Sync complete. You can preview with: mkdocs serve")


if __name__ == "__main__":
    main()
