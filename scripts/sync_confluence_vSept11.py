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
            key = str(asset_rel)
            if key not in self.downloaded_assets:
                self.client.download_file(src, full_asset)
                self.downloaded_assets.add(key)
            # Relative from the page's folder to the asset
            page_abs_dir = (self.config.docs_dir / page_path).parent
            new_src = os.path.relpath(full_asset, start=page_abs_dir)
            return new_src, asset_rel
        except Exception as e:
            logger.warning(f"Failed to process image {src}: {e}")
            return None, None

    def _process_link(self, href: str, page_path: Path, file_map: Dict[str, Path]) -> Optional[str]:
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

