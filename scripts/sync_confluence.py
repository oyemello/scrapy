yes#!/usr/bin/env python3
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
#!/usr/bin/env python3
"""
Confluence to MkDocs converter - Improved version
Converts Confluence pages to a MkDocs documentation structure.
"""
import os
import re
import sys
import time
import shutil
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from dotenv import load_dotenv
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration is invalid."""


class ConfluenceError(Exception):
    """Raised when Confluence API operations fail."""


@dataclass
class Config:
    """Application configuration"""
    base_url: str
    email: str
    token: str
    root_page_id: str
    docs_dir: Path = field(default_factory=lambda: Path("docs"))
    mkdocs_path: Path = field(default_factory=lambda: Path("mkdocs.yml"))

    @classmethod
    def from_env(cls) -> 'Config':
        """Create config from environment variables and optional .env file."""
        load_dotenv(override=False)

        required_vars = {
            'CONFLUENCE_BASE_URL': 'base_url',
            'CONFLUENCE_EMAIL': 'email',
            'CONFLUENCE_API_TOKEN': 'token',
            'CONFLUENCE_ROOT_PAGE_ID': 'root_page_id'
        }

        config_dict: Dict[str, str] = {}
        missing: List[str] = []

        for env_var, attr in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                missing.append(env_var)
            else:
                config_dict[attr] = value

        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

        # Optional overrides
        if (docs_dir := os.getenv('DOCS_DIR')):
            config_dict['docs_dir'] = Path(docs_dir)
        if (mkdocs_path := os.getenv('MKDOCS_PATH')):
            config_dict['mkdocs_path'] = Path(mkdocs_path)

        return cls(**config_dict)  # type: ignore[arg-type]

    def validate(self) -> None:
        """Validate configuration fields."""
        if not self.base_url.startswith(('http://', 'https://')):
            raise ConfigError(f"Invalid base URL: {self.base_url}")
        if '@' not in self.email:
            raise ConfigError(f"Invalid email format: {self.email}")


@dataclass
class Page:
    """Represents a Confluence page."""
    id: str
    title: str
    ancestors: List[Dict[str, str]]
    html: str
    children: List[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slugify(self.title)


class ConfluenceClient:
    """Client for interacting with Confluence API."""

    DEFAULT_RETRY_AFTER = 1
    MAX_RETRIES = 3
    PAGE_LIMIT = 100

    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_url = f"{self.base_url}/rest/api"
        self.session = self._create_session()
        self._request_count = 0

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.auth = (self.config.email, self.config.token)
        session.headers.update({
            "Accept": "application/json",
            "User-Agent": "Confluence-MkDocs-Converter/1.0"
        })
        return session

    def _handle_rate_limit(self, response: requests.Response) -> None:
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", self.DEFAULT_RETRY_AFTER))
            logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after)

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        retries = 0
        while retries < self.MAX_RETRIES:
            try:
                self._request_count += 1
                response = self.session.request(method, url, **kwargs)
                if response.status_code == 429:
                    self._handle_rate_limit(response)
                    retries += 1
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                retries += 1
                if retries >= self.MAX_RETRIES:
                    raise ConfluenceError(f"Request failed after {self.MAX_RETRIES} retries: {e}")
                logger.warning(f"Request failed, retrying ({retries}/{self.MAX_RETRIES}): {e}")
                time.sleep(2 ** retries)
        raise ConfluenceError(f"Max retries exceeded for {url}")

    def get_page(self, page_id: str) -> Page:
        url = f"{self.api_url}/content/{page_id}"
        params = {"expand": "body.view,ancestors"}
        data = self._request("GET", url, params=params).json()
        return self._parse_page(data)

    def list_children(self, page_id: str) -> List[Page]:
        pages: List[Page] = []
        start = 0
        while True:
            url = f"{self.api_url}/content/{page_id}/child/page"
            params = {"limit": self.PAGE_LIMIT, "start": start, "expand": "body.view,ancestors"}
            data = self._request("GET", url, params=params).json()
            for item in data.get("results", []):
                pages.append(self._parse_page(item))
            if data.get("_links", {}).get("next"):
                start += self.PAGE_LIMIT
            else:
                break
        return pages

    def download_file(self, url: str, dest_path: Path) -> None:
        # Normalize URL to absolute
        if url.startswith("/"):
            url = f"{self.base_url}{url}"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        resp = self._request("GET", url, stream=True)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(resp.raw, f)
        logger.debug(f"Downloaded: {url} -> {dest_path}")

    def _parse_page(self, data: dict) -> Page:
        return Page(
            id=str(data["id"]),
            title=data.get("title", f"Page {data['id']}"),
            ancestors=[{"id": str(a["id"]), "title": a.get("title", str(a["id"]))} for a in data.get("ancestors", [])],
            html=data.get("body", {}).get("view", {}).get("value", ""),
        )

    def get_stats(self) -> dict:
        return {"requests_made": self._request_count}


class PageProcessor:
    """Handles page processing: link rewrite, image download, conversion."""

    def __init__(self, client: ConfluenceClient, config: Config):
        self.client = client
        self.config = config
        self.downloaded_assets: Set[str] = set()

    def collect_tree(self, root_id: str) -> Dict[str, Page]:
        pages: Dict[str, Page] = {}
        visited: Set[str] = set()

        def dfs(page_id: str, depth: int = 0):
            if page_id in visited:
                return
            visited.add(page_id)
            logger.info(f"{'  ' * depth}Fetching page {page_id}...")
            try:
                page = self.client.get_page(page_id)
                pages[page_id] = page
                children = self.client.list_children(page_id)
                page.children = [p.id for p in children]
                for child in children:
                    pages[child.id] = child
                    dfs(child.id, depth + 1)
            except ConfluenceError as e:
                logger.error(f"Failed to fetch page {page_id}: {e}")

        dfs(root_id)
        return pages

    def build_file_map(self, pages: Dict[str, Page], root_id: str) -> Dict[str, Path]:
        file_map: Dict[str, Path] = {}
        for pid, page in pages.items():
            segments: List[str] = []
            after_root = False
            for anc in page.ancestors:
                if str(anc["id"]) == str(root_id):
                    after_root = True
                    continue
                if after_root:
                    segments.append(slugify(anc["title"]))
            if pid == str(root_id):
                filename = "index.md"
            else:
                filename = f"{page.slug}-{pid}.md"
            rel_path = (Path(*segments) / filename) if segments else Path(filename)
            file_map[pid] = rel_path
        return file_map

    def process_html(self, page: Page, html: str, page_path: Path, file_map: Dict[str, Path]) -> Tuple[str, List[Path]]:
        soup = BeautifulSoup(html, "html.parser")
        downloaded: List[Path] = []

        # Images
        for img in soup.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            if self._is_confluence_url(src):
                new_src, asset_path = self._process_image(page, src, page_path)
                if new_src:
                    img["src"] = new_src
                    if asset_path:
                        downloaded.append(asset_path)

        # Links
        for a in soup.find_all("a"):
            href = a.get("href")
            if not href:
                continue
            new_href = self._process_link(href, page_path, file_map)
            if new_href:
                a["href"] = new_href

        return str(soup), downloaded

    def _is_confluence_url(self, url: str) -> bool:
        return url.startswith(("/wiki/", "/download/")) or ("atlassian.net/wiki" in url)

    def _process_image(self, page: Page, src: str, page_path: Path) -> Tuple[Optional[str], Optional[Path]]:
        try:
            clean = src.split("?", 1)[0]
            filename = unquote(os.path.basename(clean)) or "image.png"
            asset_rel = Path("assets") / page.id / filename
            full_asset = self.config.docs_dir / asset_rel
            # Download if not already done
            key = str(asset_rel)
            if key not in self.downloaded_assets:
                self.client.download_file(src, full_asset)
                self.downloaded_assets.add(key)
            # Compute relative path from page's folder
            page_abs_dir = (self.config.docs_dir / page_path).parent
            new_src = os.path.relpath(full_asset, start=page_abs_dir)
            return new_src, asset_rel
        except Exception as e:
            logger.warning(f"Failed to process image {src}: {e}")
            return None, None

    def _process_link(self, href: str, page_path: Path, file_map: Dict[str, Path]) -> Optional[str]:
        # Skip anchors and external links (not atlassian)
        if href.startswith(("#", "mailto:")) or (href.startswith("http") and "atlassian.net/wiki" not in href):
            return None
        patterns = [r"/pages/(\d+)", r"/spaces/[A-Z0-9\-_]+/pages/(\d+)"]
        for pat in patterns:
            m = re.search(pat, href)
            if m:
                target_id = m.group(1)
                if target_id in file_map:
                    target_rel = file_map[target_id]
                    anchor = ""
                    if "#" in href:
                        anchor = "#" + href.split("#", 1)[1]
                    src_dir = (self.config.docs_dir / page_path).parent
                    dest_path = self.config.docs_dir / target_rel
                    rel = os.path.relpath(dest_path, start=src_dir)
                    return rel + anchor
        return None

    def html_to_markdown(self, html: str) -> str:
        return md(html, heading_style="ATX", strip=None)


class MkDocsWriter:
    def __init__(self, config: Config):
        self.config = config

    def write_pages(self, pages: Dict[str, Page], file_map: Dict[str, Path], processor: PageProcessor, root_id: str) -> None:
        self.config.docs_dir.mkdir(parents=True, exist_ok=True)
        for pid, page in pages.items():
            try:
                self._write_page(page, pid, file_map, processor, root_id)
            except Exception as e:
                logger.error(f"Failed to write page {pid}: {e}")

    def _write_page(self, page: Page, pid: str, file_map: Dict[str, Path], processor: PageProcessor, root_id: str) -> None:
        rel_path = file_map[pid]
        abs_path = self.config.docs_dir / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        processed_html, _ = processor.process_html(page, page.html, rel_path, file_map)
        md_content = processor.html_to_markdown(processed_html)
        if pid == str(root_id) and not re.search(r"^# ", md_content, re.MULTILINE):
            md_content = f"# {page.title}\n\n{md_content}"
        abs_path.write_text(md_content, encoding="utf-8")

    def generate_nav(self, pages: Dict[str, Page], file_map: Dict[str, Path], root_id: str) -> List:
        def item(pid: str) -> Dict:
            return {pages[pid].title: str(file_map[pid])}

        def build(pid: str) -> List:
            items: List = []
            for cid in pages[pid].children:
                child = pages[cid]
                if child.children:
                    section = [item(cid)]
                    section.extend(build(cid))
                    items.append({child.title: section})
                else:
                    items.append(item(cid))
            return items

        nav: List = [{"Home": str(file_map[root_id])}]
        nav.extend(build(root_id))
        return nav

    def update_mkdocs_config(self, site_name: str, nav: List) -> None:
        base_cfg = {
            "site_name": site_name,
            "theme": {"name": "material"},
            "docs_dir": str(self.config.docs_dir),
            "plugins": ["search", "minify"],
            "markdown_extensions": ["admonition", "codehilite", "meta", "toc", "tables"],
        }
        if self.config.mkdocs_path.exists():
            try:
                with open(self.config.mkdocs_path, "r", encoding="utf-8") as f:
                    existing = yaml.safe_load(f) or {}
                    base_cfg.update(existing)
            except Exception as e:
                logger.warning(f"Could not load existing mkdocs.yml: {e}")
        base_cfg["nav"] = nav
        with open(self.config.mkdocs_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(base_cfg, f, sort_keys=False, allow_unicode=True)
        logger.info(f"Updated MkDocs config: {self.config.mkdocs_path}")


def slugify(text: str) -> str:
    if not text:
        return "page"
    s = text.strip().lower()
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^a-z0-9\-_]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "page"


def main() -> None:
    try:
        cfg = Config.from_env()
        cfg.validate()
        logger.info("Starting Confluence → MkDocs sync...")
        logger.info(f"Root page ID: {cfg.root_page_id}")
        logger.info(f"Docs directory: {cfg.docs_dir}")

        client = ConfluenceClient(cfg)
        processor = PageProcessor(client, cfg)
        writer = MkDocsWriter(cfg)

        logger.info("Collecting pages from Confluence...")
        pages = processor.collect_tree(cfg.root_page_id)
        logger.info(f"Collected {len(pages)} pages")
        if not pages:
            logger.error("No pages found")
            sys.exit(1)

        logger.info("Building file map...")
        file_map = processor.build_file_map(pages, cfg.root_page_id)

        logger.info("Writing Markdown files...")
        writer.write_pages(pages, file_map, processor, cfg.root_page_id)

        logger.info("Generating nav and updating mkdocs.yml...")
        site_name = pages.get(cfg.root_page_id, Page("", "Confluence Docs", [], "")).title
        nav = writer.generate_nav(pages, file_map, cfg.root_page_id)
        writer.update_mkdocs_config(site_name, nav)

        stats = client.get_stats()
        logger.info("Sync complete!")
        logger.info(f"  Pages: {len(pages)}")
        logger.info(f"  API requests: {stats['requests_made']}")
        logger.info(f"  Assets downloaded: {len(processor.downloaded_assets)}")
        logger.info("Preview locally: mkdocs serve")

    except ConfigError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except ConfluenceError as e:
        logger.error(f"Confluence API error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
