#!/usr/bin/env python3
"""
Confluence → MkDocs (vSept15 v2)

Goals:
- Do NOT commit generated Markdown to the repo.
- Generate a flat site into `.generated_docs/` and deploy using a generated mkdocs config.
- Deep-inline children up to MAX_LINK_DEPTH (default 2) within the parent page.
- Resolve Confluence tiny/display links to local anchors or pages as appropriate.
- Users only edit .env for credentials and `MAX_LINK_DEPTH`.
"""
from __future__ import annotations

import os
import re
import sys
import time
import shutil
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from urllib.parse import urlparse, unquote
import html as htmlesc

import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md
from dotenv import load_dotenv
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


class ConfluenceError(Exception):
    pass


@dataclass
class Config:
    base_url: str
    email: str
    token: str
    root_page_id: str
    # Generated output root; not user-configurable by env to keep UX simple
    out_dir: Path = field(default_factory=lambda: Path('.generated_docs'))
    # Depth settings
    follow_links: bool = True
    max_link_depth: int = 4

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(override=False)
        wanted = {
            'CONFLUENCE_BASE_URL': 'base_url',
            'CONFLUENCE_EMAIL': 'email',
            'CONFLUENCE_API_TOKEN': 'token',
            'CONFLUENCE_ROOT_PAGE_ID': 'root_page_id',
        }
        data: Dict[str, object] = {}
        missing: List[str] = []
        for k, a in wanted.items():
            v = os.getenv(k)
            if not v:
                missing.append(k)
            else:
                data[a] = v
        if missing:
            raise ConfigError("Missing env: " + ", ".join(missing))

        fl = os.getenv('FOLLOW_LINKS')
        if fl is not None:
            data['follow_links'] = str(fl).strip().lower() in ("1", "true", "yes", "y")
        mld = os.getenv('MAX_LINK_DEPTH')
        if mld:
            try:
                data['max_link_depth'] = max(0, int(mld))
            except ValueError:
                pass
        cfg = cls(**data)  # type: ignore[arg-type]
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.base_url.startswith(('http://', 'https://')):
            raise ConfigError('CONFLUENCE_BASE_URL must be http(s) URL')
        if '@' not in self.email:
            raise ConfigError('CONFLUENCE_EMAIL looks invalid')


@dataclass
class Page:
    id: str
    title: str
    ancestors: List[Dict[str, str]]
    html: str
    children: List[str] = field(default_factory=list)
    link_depth: int = 0
    discovered_from: Optional[str] = None

    @property
    def slug(self) -> str:
        return slugify(self.title)


class ConfluenceClient:
    DEFAULT_RETRY_AFTER = 1
    MAX_RETRIES = 3
    PAGE_LIMIT = 100

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = self._normalize_base_url(cfg.base_url)
        self.api = f"{self.base_url}/rest/api"
        self.session = requests.Session()
        self.session.auth = (cfg.email, cfg.token)
        self.session.headers.update({"Accept": "application/json", "User-Agent": "scrapy-vsept15-v2"})
        self._count = 0
        self._host = urlparse(self.base_url).netloc
        self._resolve_cache: Dict[str, Optional[str]] = {}

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """Normalize various Confluence URLs to a proper API base.

        Accepts full share/tiny links like
        https://<host>/wiki/pages/tinyurl.action?urlIdentifier=XXXX
        and reduces to https://<host>/wiki for Cloud instances.
        """
        try:
            p = urlparse(url)
            if not p.scheme or not p.netloc:
                return url.rstrip('/')
            host = f"{p.scheme}://{p.netloc}"
            # Cloud typically uses /wiki for UI & REST API
            if '/wiki' in (p.path or ''):
                return f"{host}/wiki"
            # Fallback to host root
            return host.rstrip('/')
        except Exception:
            return url.rstrip('/')

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        for i in range(self.MAX_RETRIES):
            try:
                self._count += 1
                r = self.session.request(method, url, **kw)
                if r.status_code == 429:
                    time.sleep(int(r.headers.get('Retry-After', self.DEFAULT_RETRY_AFTER)))
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                if i == self.MAX_RETRIES - 1:
                    raise ConfluenceError(str(e))
                time.sleep(2 ** (i + 1))
        raise ConfluenceError("unreachable")

    def _get(self, path: str, params: Optional[Dict] = None) -> dict:
        url = path if path.startswith('http') else f"{self.api}{path}"
        return self._req('GET', url, params=params).json()

    def _abs_url(self, url: str) -> str:
        if url.startswith('/'):
            return f"{self.base_url}{url}"
        return url

    def _extract_id_from_url(self, url: str) -> Optional[str]:
        m = re.search(r"/pages/(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"[?&]pageId=(\d+)", url)
        if m:
            return m.group(1)
        m = re.search(r"/content/(\d+)", url)
        if m:
            return m.group(1)
        return None

    def resolve_page_id(self, href: str) -> Optional[str]:
        if not href:
            return None
        if href in self._resolve_cache:
            return self._resolve_cache[href]
        try:
            p = urlparse(href)
            if p.scheme and p.netloc and (self._host not in p.netloc):
                self._resolve_cache[href] = None
                return None
            if pid := self._extract_id_from_url(href):
                self._resolve_cache[href] = pid
                return pid
            if not p.scheme and not p.netloc:
                absu = self._abs_url(href)
            else:
                absu = href
            if urlparse(absu).netloc and (self._host not in urlparse(absu).netloc):
                self._resolve_cache[href] = None
                return None
            try:
                r = self._req('GET', absu, allow_redirects=True)
                final_url = r.url
            except ConfluenceError:
                try:
                    r = self._req('HEAD', absu, allow_redirects=True)
                    final_url = r.url
                except Exception:
                    final_url = absu
            pid = self._extract_id_from_url(final_url)
            self._resolve_cache[href] = pid
            return pid
        except Exception:
            self._resolve_cache[href] = None
            return None

    def get_page(self, page_id: str, link_depth: int = 0) -> Page:
        data = self._get(f"/content/{page_id}", params={"expand": "body.view,ancestors"})
        return Page(
            id=str(data['id']),
            title=data.get('title', f"Page {page_id}"),
            ancestors=[{"id": str(a['id']), "title": a.get('title', str(a['id']))} for a in data.get('ancestors', [])],
            html=data.get('body', {}).get('view', {}).get('value', ''),
            link_depth=link_depth,
        )

    def list_children(self, page_id: str, link_depth: int = 0) -> List[Page]:
        out: List[Page] = []
        start = 0
        while True:
            data = self._get(f"/content/{page_id}/child/page", params={"limit": self.PAGE_LIMIT, "start": start, "expand": "body.view,ancestors"})
            for item in data.get('results', []):
                out.append(Page(
                    id=str(item['id']),
                    title=item.get('title', str(item['id'])),
                    ancestors=[{"id": str(a['id']), "title": a.get('title', str(a['id']))} for a in item.get('ancestors', [])],
                    html=item.get('body', {}).get('view', {}).get('value', ''),
                    link_depth=link_depth,
                ))
            if data.get('_links', {}).get('next'):
                start += self.PAGE_LIMIT
            else:
                break
        return out

    def download(self, url: str, dest: Path) -> None:
        if url.startswith('/'):
            url = f"{self.base_url}{url}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._req('GET', url, stream=True) as r:
            with open(dest, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

    def is_confluence_asset(self, url: str) -> bool:
        p = urlparse(url)
        # Relative URL -> assume it's a Confluence-hosted asset
        if not p.netloc:
            return p.path.startswith(('/wiki/', '/download/', '/s/', 'download/')) or True
        # Same host and common asset paths
        return (self._host in p.netloc) and (
            p.path.startswith(('/', '/wiki/', '/download/', '/s/'))
        )

    def stats(self) -> Dict[str, int]:
        return {"requests": self._count}


class Processor:
    def __init__(self, cfg: Config, client: ConfluenceClient):
        self.cfg = cfg
        self.client = client
        self.assets: Set[str] = set()
        self.discovered_from: Dict[str, str] = {}

    def collect(self, root_id: str) -> Dict[str, Page]:
        from collections import deque
        pages: Dict[str, Page] = {}
        visited: Set[str] = set()
        q = deque([(str(root_id), 0)])
        while q:
            pid, ldepth = q.popleft()
            if pid in visited:
                continue
            visited.add(pid)
            try:
                logger.info("Fetching %s (link-depth=%d)", pid, ldepth)
                page = self.client.get_page(pid, ldepth)
                pages[pid] = page
                # Enqueue children without increasing link-depth
                children = self.client.list_children(pid, ldepth)
                page.children = [c.id for c in children]
                for ch in children:
                    if ch.id not in visited:
                        pages[ch.id] = ch
                        q.append((ch.id, ldepth))
                if self.cfg.follow_links and ldepth < self.cfg.max_link_depth:
                    linked_ids = self._extract_linked_page_ids(page.html)
                    logger.info("Found %d linked pages in %s", len(linked_ids), pid)
                    for linked in linked_ids:
                        if linked not in visited:
                            # remember who referenced it first
                            if linked not in self.discovered_from:
                                self.discovered_from[linked] = pid
                            logger.info("Queuing linked page %s at depth %d", linked, ldepth + 1)
                            q.append((linked, ldepth + 1))
            except Exception as e:
                logger.error("Failed to fetch page %s: %s", pid, e)
        return pages

    def _extract_linked_page_ids(self, html: str) -> Set[str]:
        ids: Set[str] = set()
        if not html:
            return ids
        try:
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a'):
                href = a.get('href')
                if not href:
                    continue
                m = re.search(r"/pages/(\d+)", href) or re.search(r"/spaces/[A-Z0-9\-_]+/pages/(\d+)", href)
                if m:
                    ids.add(m.group(1)); continue
                if ('/x/' in href) or ('/wiki/x/' in href) or ('pageId=' in href) or ('/display/' in href):
                    if (pid := self.client.resolve_page_id(href)):
                        ids.add(pid)
        except Exception:
            pass
        return ids

    def clean_html(self, page: Page, html: str, page_path: Path) -> Tuple[str, List[Path]]:
        soup = BeautifulSoup(html or '', 'html.parser')
        self._normalize_headings(soup)
        for t in soup.find_all(True):
            if 'style' in t.attrs:
                del t['style']
        downloads: List[Path] = []
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-image-src') or img.get('data-src')
            if not src:
                alias = img.get('data-linked-resource-default-alias') or img.get('data-media-services-file-name')
                container = img.get('data-linked-resource-container-id')
                if container and alias:
                    src = f"/download/attachments/{container}/{alias}"
                else:
                    continue
            # Make absolute for fetching decision
            abs_src = self.client._abs_url(src)
            # Skip known emoticon sprite paths which often 404 and aren't critical
            if '/images/icons/emoticons/' in abs_src:
                continue
            if self.client.is_confluence_asset(abs_src) or src.startswith(('download/', 'wiki/download', '/download/', '/wiki/s/', '/wiki/download/')):
                new_src, asset_rel = self._download_asset(page, abs_src, page_path)
                if new_src:
                    img['src'] = new_src
                if asset_rel:
                    downloads.append(asset_rel)
        # Links are rewritten later when we know anchors and file layout
        return str(soup), downloads

    def _normalize_headings(self, soup: BeautifulSoup) -> None:
        h1s = soup.find_all('h1')
        for i, h in enumerate(h1s):
            if i > 0 and isinstance(h, Tag):
                h.name = 'h2'

    def _download_asset(self, page: Page, src: str, page_path: Path) -> Tuple[Optional[str], Optional[Path]]:
        try:
            clean = src.split('?', 1)[0]
            filename = unquote(os.path.basename(clean)) or 'asset'
            rel = Path('assets') / page.id / filename
            full = self.cfg.out_dir / rel
            key = str(rel)
            if key not in self.assets:
                self.client.download(src, full)
                self.assets.add(key)
            page_dir = (self.cfg.out_dir / page_path).parent
            new_src = os.path.relpath(full, start=page_dir)
            return new_src, rel
        except Exception as e:
            logger.warning("asset download failed %s: %s", src, e)
            return None, None


def slugify(text: str) -> str:
    if not text:
        return 'page'
    s = text.strip().lower()
    s = re.sub(r"[\s/]+", '-', s)
    s = re.sub(r"[^a-z0-9\-_]", '', s)
    s = re.sub(r"-+", '-', s).strip('-')
    return s or 'page'


def shift_headings(md_text: str, shift: int) -> str:
    if shift <= 0:
        return md_text
    def repl(m: re.Match[str]) -> str:
        hashes = m.group(1)
        rest = m.group(2)
        new = min(6, len(hashes) + shift)
        return '#' * new + rest
    return re.sub(r"^(#{1,6})(\s+.+)$", repl, md_text, flags=re.MULTILINE)


def strip_first_h1(md_text: str) -> str:
    m = re.search(r"^#\s+.+\n?", md_text, flags=re.MULTILINE)
    if not m:
        return md_text
    start, end = m.span()
    rest = md_text[end:]
    return re.sub(r"^\n", "", rest, count=1)


class InlineWriter:
    def __init__(self, cfg: Config, proc: Processor, pages: Dict[str, Page], root_id: str):
        self.cfg = cfg
        self.proc = proc
        self.pages = pages
        self.root_id = str(root_id)
        self.file_map: Dict[str, Path] = {}  # page id -> output path (for top-level pages only)
        self.inlined_ids_by_file: Dict[Path, Set[str]] = {}
        # Map of first-parent for linked pages
        self.discovered_from: Dict[str, str] = getattr(proc, 'discovered_from', {})

    def build_layout(self, root_id: str) -> None:
        # Map every page to an output file. Linked pages go under linked-content/depth-N/
        for pid, page in self.pages.items():
            if page.link_depth > 0:
                segs: List[str] = ['linked-content']
                if page.link_depth > 1:
                    segs.append(f'depth-{page.link_depth}')
                rel = Path(*segs) / f"{page.slug}-{pid}.md"
            else:
                # Place hierarchy pages at root (flat); could be extended to ancestor folders if needed
                rel = Path('overview.md') if pid == str(root_id) else Path(f"{page.slug}-{pid}.md")
            self.file_map[pid] = rel

    def _collect_inlined(self, pid: str, max_depth: int) -> Set[str]:
        # ids that will be inlined inside pid's file (excluding pid itself)
        out: Set[str] = set()
        def rec(cur: str, depth: int) -> None:
            if depth >= max_depth:
                return
            for ch in self.pages.get(cur, Page('', '', [], '')).children:
                out.add(ch)
                rec(ch, depth + 1)
        rec(pid, 0)
        return out

    def render_all(self, root_id: str) -> None:
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        # Copy assets from repo 'site_assets' (preferred) or legacy 'docs/assets'
        dest = self.cfg.out_dir / 'assets'
        if dest.exists():
            shutil.rmtree(dest)
        src_candidates = [Path('site_assets'), Path('docs/assets')]
        for src in src_candidates:
            if src.exists():
                shutil.copytree(src, dest)
                break

        # No inlining; clear sets
        self.inlined_ids_by_file = {path: set() for path in self.file_map.values()}

        # Render pages (each as a standalone file)
        for pid, rel in self.file_map.items():
            self._write_single_page(pid, rel)

        # Write homepage
        self._write_homepage(root_id)

        # Compose and write mkdocs config into the out_dir
        self._write_mkdocs_config(root_id)

    def _to_md(self, page: Page, rel_path: Path) -> str:
        cleaned, _ = self.proc.clean_html(page, page.html, rel_path)
        return md(cleaned or '', heading_style='ATX', strip=None)

    def _write_single_page(self, pid: str, rel: Path) -> None:
        page = self.pages[pid]
        raw_md = self._to_md(page, rel)
        body = strip_first_h1(raw_md)
        header = f"# {page.title}\n\n"
        breadcrumbs = self._build_breadcrumbs(pid, rel)
        md_text = header + breadcrumbs + body

        (self.cfg.out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        # Rewrite links to local pages
        md_text = self._rewrite_links_for_file(md_text, rel)
        (self.cfg.out_dir / rel).write_text(md_text, encoding='utf-8')

    def _rel_href(self, src: Path, dest: Path) -> str:
        """Generate relative path from src file to dest file for breadcrumbs."""
        src_dir = (self.cfg.out_dir / src).parent
        dest_file = self.cfg.out_dir / dest
        rel_path = os.path.relpath(dest_file.as_posix(), start=src_dir.as_posix()).replace('\\', '/')
        return rel_path

    def _build_breadcrumbs(self, pid: str, rel: Path) -> str:
        chain: List[Tuple[str, Path]] = []
        chain.append(("Home", Path('index.md')))
        chain.append((self.pages.get(self.root_id, Page('', 'Overview', [], '')).title or 'Overview', self.file_map.get(self.root_id, Path('overview.md'))))
        if pid != self.root_id:
            p = self.pages[pid]
            # Hierarchy ancestors after root
            if p.link_depth == 0:
                seen_root = False
                for anc in p.ancestors:
                    aid = str(anc.get('id'))
                    if aid == self.root_id:
                        seen_root = True
                        continue
                    if seen_root and aid in self.file_map and aid in self.pages:
                        chain.append((self.pages[aid].title, self.file_map[aid]))
            else:
                # Follow discovered_from chain up to a hierarchy page or root
                current = pid
                hops: List[str] = []
                while current in self.discovered_from:
                    parent = self.discovered_from[current]
                    if parent in hops:  # prevent cycles
                        break
                    hops.append(parent)
                    if parent in self.pages and self.pages[parent].link_depth == 0:
                        break
                    current = parent
                for hid in reversed(hops):
                    if hid in self.file_map and hid in self.pages:
                        chain.append((self.pages[hid].title, self.file_map[hid]))
        # Build HTML
        parts: List[str] = []
        for title, path in chain:
            href = self._rel_href(rel, path)
            parts.append(f'<a href="{htmlesc.escape(href)}">{htmlesc.escape(title)}</a>')
        parts.append(htmlesc.escape(self.pages[pid].title))
        return '<nav class="breadcrumbs">' + ' / '.join(parts) + '</nav>\n\n'

    def _rewrite_links_for_file(self, md_text: str, rel: Path) -> str:
        # Reverse-map: id -> top-level file path
        id_to_file: Dict[str, Path] = {pid: p for pid, p in self.file_map.items()}

        def repl(m: re.Match[str]) -> str:
            prefix = m.group('bang')
            text = m.group('text')
            href = m.group('href')
            if prefix == '!':
                return m.group(0)  # images untouched
            # Resolve possible Confluence IDs
            pid: Optional[str] = None
            # Direct ID in URL
            m_id = re.search(r"/pages/(\d+)", href) or re.search(r"/spaces/[A-Z0-9\-_]+/pages/(\d+)", href) or re.search(r"[?&]pageId=(\d+)", href)
            if m_id:
                pid = m_id.group(1)
            else:
                # Try tiny/display resolution if looks like confluence path
                if 'atlassian.net' in href or '/wiki/' in href or '/display/' in href:
                    pid = self.proc.client.resolve_page_id(href)
            if not pid:
                return m.group(0)
            # Link to the mapped file if known; compute site-relative path (pretty URLs)
            if pid in id_to_file:
                relpath = self._site_rel(rel, id_to_file[pid])
                return f"[{text}]({relpath})"
            return m.group(0)

        pattern = re.compile(r"(?P<bang>!?)\[(?P<text>[^\]]*)\]\((?P<href>[^)]+)\)")
        return pattern.sub(repl, md_text)

    def _site_rel(self, src_file: Path, dest_file: Path) -> str:
        """Compute docs-relative .md path between Markdown files."""
        src_dir = (self.cfg.out_dir / src_file).parent
        dest_abs = self.cfg.out_dir / dest_file
        return os.path.relpath(dest_abs.as_posix(), start=src_dir.as_posix()).replace('\\', '/')

    def _write_homepage(self, root_id: str) -> None:
        root = self.pages.get(root_id)
        title = (root.title if root else 'Documentation')
        tiles: List[str] = []
        if root:
            for cid in root.children:
                if cid not in self.pages:
                    continue
                file_path = f"{self.pages[cid].slug}-{cid}.md"
                href = file_path[:-3] + '/'  # extensionless URL for MkDocs
                tiles.append(("<a class=\"category-card\" href=\"{href}\">"
                              "  <div class=\"card-title\">{title}</div>"
                              "</a>").format(href=href, title=self.pages[cid].title))
        grid_html = "\n".join(tiles)
        homepage_md = (f"# {title}\n\n"
                       "Welcome. Choose a category to get started:\n\n"
                       "<div class=\"category-grid\">\n"
                       f"{grid_html}\n"
                       "</div>\n")
        (self.cfg.out_dir / 'index.md').write_text(homepage_md, encoding='utf-8')

    def _write_mkdocs_config(self, root_id: str) -> None:
        # Start from existing mkdocs.yml if present to keep theme
        base = {
            'site_name': self.pages.get(root_id, Page('', 'scrapy', [], '')).title or 'scrapy',
            'theme': {
                'name': 'material',
                'features': [
                    'navigation.tracking',
                    'navigation.sections',
                    'navigation.expand',
                    'search.highlight',
                    'search.suggest',
                    'content.code.copy',
                ],
            },
            # docs_dir is relative to this config file's folder (out_dir)
            'docs_dir': '.',
            # site_dir must be outside docs_dir
            'site_dir': '../site',
            'plugins': ['search', 'minify'],
        }
        existing_md_ext = []
        if Path('mkdocs.yml').exists():
            try:
                with open('mkdocs.yml', 'r', encoding='utf-8') as f:
                    existing = yaml.safe_load(f) or {}
                    # Copy look & feel but not docs_dir/nav
                    for k in ('site_name', 'theme', 'plugins', 'site_url', 'extra_css', 'extra_javascript', 'extra', 'markdown_extensions'): 
                        if k in existing:
                            base[k] = existing[k]
                    existing_md_ext = existing.get('markdown_extensions', []) or []
            except Exception as e:
                logger.warning('Could not read mkdocs.yml: %s', e)

        # Ensure overrides.css if present in repo assets gets included
        if (self.cfg.out_dir / 'assets' / 'overrides.css').exists():
            ec = base.get('extra_css', []) or []
            if 'assets/overrides.css' not in ec:
                ec.append('assets/overrides.css')
            base['extra_css'] = ec

        # Build nav: Home, Overview, hierarchical main pages; include linked pages under the parent that referenced them
        nav: List = [{"Home": 'index.md'}]
        overview_path = self.file_map.get(root_id, Path('overview.md')).as_posix()
        nav.append({"Overview": overview_path})

        def item(pid: str) -> Dict:
            p = self.pages[pid]
            return {p.title: self.file_map[pid].as_posix()}

        def linked_entries(parent_id: str) -> List:
            """Build recursive 'Linked Pages' entries for a parent_id, filtering out
            hierarchy pages and deduplicating.
            """
            results: List = []
            # Only include pages discovered from this parent with link_depth > 0
            linked_here = [lid for lid, parent in self.discovered_from.items()
                           if parent == parent_id and lid in self.pages and self.pages[lid].link_depth > 0]
            for lid in sorted(linked_here, key=lambda x: self.pages[x].title):
                # Build possible nested linked pages under this linked page
                children = linked_entries(lid)
                if children:
                    results.append({self.pages[lid].title: [item(lid), {"Linked Pages": children}]})
                else:
                    results.append(item(lid))
            return results

        def build(pid: str) -> List:
            arr: List = []
            # For each hierarchy child, build a section including its own children and its linked pages group
            for cid in self.pages[pid].children:
                ch = self.pages[cid]
                if ch.link_depth > 0:
                    continue
                subsection: List = []
                subsection.append(item(cid))
                if ch.children:
                    subsection.extend(build(cid))
                lnk = linked_entries(cid)
                if lnk:
                    subsection.append({"Linked Pages": lnk})
                if len(subsection) == 1 and not ch.children and not lnk:
                    arr.append(item(cid))
                else:
                    arr.append({ch.title: subsection})
            return arr

        root = self.pages.get(root_id)
        if root:
            nav.extend(build(root_id))

        base['nav'] = nav

        with open(self.cfg.out_dir / 'mkdocs.yml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(base, f, sort_keys=False, allow_unicode=True)


def _validate_internal_links(out_dir: Path) -> None:
    """Scan generated Markdown and basic HTML for internal links and ensure targets exist.

    Rules:
    - Markdown links to files must resolve to an existing .md within out_dir (after join).
    - Pretty links ending with '/' (e.g., 'foo/') are mapped to 'foo.md'.
    - HTML anchor hrefs are validated similarly; external links are ignored.
    Raise ConfluenceError if any invalid internal links are found.
    """
    import re
    errors: List[str] = []

    def is_external(href: str) -> bool:
        return href.startswith(('http://', 'https://', 'mailto:', 'tel:'))

    def as_target(src_md: Path, href: str, *, html: bool = False) -> Optional[Path]:
        # Ignore pure anchor
        if not href or href.startswith('#'):
            return None
        # Strip fragment
        h = href.split('#', 1)[0]
        if is_external(h):
            return None
        # Normalize path; HTML breadcrumbs use pretty folders, Markdown uses .md
        if html:
            # Resolve relative to the page's URL base (strip suffix)
            base = (out_dir / src_md).with_suffix('')
            # If ends with '/', drop it and append '.md'
            if h.endswith('/'):
                h2 = h[:-1] + '.md'
            else:
                # If no extension, assume .md
                h2 = h if h.lower().endswith('.md') else h + '.md'
            abs_path = (base / h2).resolve()
        else:
            src_dir = (out_dir / src_md).parent
            if h.endswith('/'):
                h2 = h[:-1] + '.md'
            else:
                h2 = h
            if '.' not in os.path.basename(h2):
                h2 = h2 + '.md'
            abs_path = (src_dir / h2).resolve()
        try:
            abs_path.relative_to(out_dir.resolve())
        except Exception:
            return abs_path
        return abs_path

    md_link_re = re.compile(r"(?P<bang>!?)\[(?P<text>[^\]]*)\]\((?P<href>[^)]+)\)")
    html_href_re = re.compile(r"href=\"([^\"]+)\"")

    for md_file in out_dir.rglob('*.md'):
        text = md_file.read_text(encoding='utf-8', errors='ignore')
        # Markdown links
        for m in md_link_re.finditer(text):
            if m.group('bang') == '!':
                continue
            href = m.group('href').strip()
            tgt = as_target(md_file.relative_to(out_dir), href)
            if tgt and not tgt.exists():
                errors.append(f"{md_file}: unresolved link -> {href} (expected {tgt})")
        # HTML hrefs
        for hm in html_href_re.finditer(text):
            href = hm.group(1).strip()
            tgt = as_target(md_file.relative_to(out_dir), href, html=True)
            if tgt and not tgt.exists():
                errors.append(f"{md_file}: unresolved HTML href -> {href} (expected {tgt})")

    if errors:
        for e in errors[:50]:
            logger.error(e)
        if len(errors) > 50:
            logger.error("... and %d more", len(errors) - 50)
        raise ConfluenceError(f"Invalid internal links detected: {len(errors)} problems")


def run_mkdocs(out_dir: Path, deploy: bool = True) -> None:
    cfg_path = out_dir / 'mkdocs.yml'
    # Validate links before building/deploying
    _validate_internal_links(out_dir)
    if deploy:
        cmd = ['mkdocs', 'gh-deploy', '--force', '-f', str(cfg_path)]
    else:
        cmd = ['mkdocs', 'build', '--strict', '-f', str(cfg_path)]
    logger.info('Running: %s', ' '.join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    try:
        cfg = Config.from_env()
        client = ConfluenceClient(cfg)
        proc = Processor(cfg, client)

        # Fresh output directory
        if cfg.out_dir.exists():
            shutil.rmtree(cfg.out_dir)
        cfg.out_dir.mkdir(parents=True, exist_ok=True)

        logger.info('Collecting pages...')
        pages = proc.collect(cfg.root_page_id)
        if not pages:
            logger.error('No pages collected'); sys.exit(1)

        writer = InlineWriter(cfg, proc, pages, cfg.root_page_id)
        writer.build_layout(cfg.root_page_id)
        writer.render_all(cfg.root_page_id)

        stats = client.stats()
        logger.info('Collected pages=%d requests=%d assets=%d', len(pages), stats['requests'], len(proc.assets))

        # Deploy using generated config
        run_mkdocs(cfg.out_dir, deploy=True)
        logger.info('Deploy completed.')
    except (ConfigError, ConfluenceError) as e:
        logger.error(str(e)); sys.exit(1)
    except KeyboardInterrupt:
        logger.info('Interrupted'); sys.exit(130)
    except subprocess.CalledProcessError as e:
        logger.error('Build/Deploy failed: %s', e); sys.exit(e.returncode)
    except Exception as e:
        logger.exception('Unexpected error: %s', e); sys.exit(1)


if __name__ == '__main__':
    main()