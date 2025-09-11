#!/usr/bin/env python3
"""
Confluence → MkDocs (vSept11.2)
Focus: robust BeautifulSoup-based HTML cleanup + full-tree navigation
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
    docs_dir: Path = field(default_factory=lambda: Path("docs"))
    mkdocs_path: Path = field(default_factory=lambda: Path("mkdocs.yml"))

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(override=False)
        wanted = {
            'CONFLUENCE_BASE_URL': 'base_url',
            'CONFLUENCE_EMAIL': 'email',
            'CONFLUENCE_API_TOKEN': 'token',
            'CONFLUENCE_ROOT_PAGE_ID': 'root_page_id',
        }
        data: Dict[str, str] = {}
        missing: List[str] = []
        for k, a in wanted.items():
            v = os.getenv(k)
            if not v:
                missing.append(k)
            else:
                data[a] = v
        if missing:
            raise ConfigError("Missing env: " + ", ".join(missing))
        if d := os.getenv('DOCS_DIR'):
            data['docs_dir'] = Path(d)  # type: ignore
        if m := os.getenv('MKDOCS_PATH'):
            data['mkdocs_path'] = Path(m)  # type: ignore
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

    @property
    def slug(self) -> str:
        return slugify(self.title)


class ConfluenceClient:
    DEFAULT_RETRY_AFTER = 1
    MAX_RETRIES = 3
    PAGE_LIMIT = 100

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip('/')
        self.api = f"{self.base_url}/rest/api"
        self.session = requests.Session()
        self.session.auth = (cfg.email, cfg.token)
        self.session.headers.update({"Accept": "application/json", "User-Agent": "scrapy-vsept11.2"})
        self._count = 0
        self._host = urlparse(self.base_url).netloc

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

    def get_page(self, page_id: str) -> Page:
        data = self._get(f"/content/{page_id}", params={"expand": "body.view,ancestors"})
        return Page(
            id=str(data['id']),
            title=data.get('title', f"Page {page_id}"),
            ancestors=[{"id": str(a['id']), "title": a.get('title', str(a['id']))} for a in data.get('ancestors', [])],
            html=data.get('body', {}).get('view', {}).get('value', ''),
        )

    def list_children(self, page_id: str) -> List[Page]:
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
                ))
            if data.get('_links', {}).get('next'):
                start += self.PAGE_LIMIT
            else:
                break
        return out

    def download(self, url: str, dest: Path) -> None:
        # Absolute URL: ok. Relative: prefix base host
        if url.startswith('/'):
            url = f"{self.base_url}{url}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._req('GET', url, stream=True) as r:
            with open(dest, 'wb') as f:
                shutil.copyfileobj(r.raw, f)

    def is_confluence_asset(self, url: str) -> bool:
        p = urlparse(url)
        if not p.netloc:
            return p.path.startswith(('/wiki/', '/download/'))
        return (self._host in p.netloc) and p.path.startswith(('/', '/wiki/', '/download/'))

    def stats(self) -> Dict[str, int]:
        return {"requests": self._count}


class Processor:
    def __init__(self, cfg: Config, client: ConfluenceClient):
        self.cfg = cfg
        self.client = client
        self.assets: Set[str] = set()

    def collect(self, root_id: str) -> Dict[str, Page]:
        pages: Dict[str, Page] = {}
        seen: Set[str] = set()

        def dfs(pid: str, depth: int = 0):
            if pid in seen:
                return
            seen.add(pid)
            logger.info("%sFetching %s", "  " * depth, pid)
            page = self.client.get_page(pid)
            pages[pid] = page
            children = self.client.list_children(pid)
            page.children = [c.id for c in children]
            for ch in children:
                pages[ch.id] = ch
                dfs(ch.id, depth + 1)

        dfs(root_id)
        return pages

    def build_map(self, pages: Dict[str, Page], root_id: str) -> Dict[str, Path]:
        m: Dict[str, Path] = {}
        for pid, page in pages.items():
            segs: List[str] = []
            after_root = False
            for anc in page.ancestors:
                if str(anc['id']) == str(root_id):
                    after_root = True
                    continue
                if after_root:
                    segs.append(slugify(anc['title']))
            filename = 'overview.md' if pid == str(root_id) else f"{page.slug}-{pid}.md"
            m[pid] = (Path(*segs) / filename) if segs else Path(filename)
        return m

    def clean_html(self, page: Page, html: str, page_path: Path, fmap: Dict[str, Path]) -> Tuple[str, List[Path]]:
        soup = BeautifulSoup(html, 'html.parser')
        # Normalize headings: keep only one h1
        self._normalize_headings(soup)
        # Strip inline styles
        for t in soup.find_all(True):
            if 'style' in t.attrs:
                del t['style']
        # Keep root-page index visible (do not remove lists)
        # Images
        downloads: List[Path] = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if not src:
                continue
            if self.client.is_confluence_asset(src):
                new_src, asset_rel = self._download_asset(page, src, page_path)
                if new_src:
                    img['src'] = new_src
                if asset_rel:
                    downloads.append(asset_rel)
        # Links
        for a in soup.find_all('a'):
            href = a.get('href')
            if not href:
                continue
            new = self._rewrite_link(href, page_path, fmap)
            if new:
                a['href'] = new
        return str(soup), downloads

    def _normalize_headings(self, soup: BeautifulSoup) -> None:
        h1s = soup.find_all('h1')
        for i, h in enumerate(h1s):
            if i > 0 and isinstance(h, Tag):
                h.name = 'h2'

    def _remove_redundant_root_list(self, soup: BeautifulSoup, child_stems: List[str]) -> None:
        for lst in soup.find_all(['ol', 'ul']):
            anchors = lst.find_all('a')
            if not anchors:
                continue
            matches = 0
            for a in anchors:
                text = slugify((a.get_text() or '').strip())
                if any(text.startswith(stem.split('-')[0]) for stem in child_stems):
                    matches += 1
            if matches >= max(2, len(anchors) // 2):
                lst.decompose()
                break

    def _download_asset(self, page: Page, src: str, page_path: Path) -> Tuple[Optional[str], Optional[Path]]:
        try:
            clean = src.split('?', 1)[0]
            filename = unquote(os.path.basename(clean)) or 'asset'
            rel = Path('assets') / page.id / filename
            full = self.cfg.docs_dir / rel
            key = str(rel)
            if key not in self.assets:
                self.client.download(src, full)
                self.assets.add(key)
            page_dir = (self.cfg.docs_dir / page_path).parent
            new_src = os.path.relpath(full, start=page_dir)
            return new_src, rel
        except Exception as e:
            logger.warning("asset download failed %s: %s", src, e)
            return None, None

    def _rewrite_link(self, href: str, page_path: Path, fmap: Dict[str, Path]) -> Optional[str]:
        if href.startswith(('#', 'mailto:')):
            return None
        # external non-confluence
        p = urlparse(href)
        if p.scheme and 'atlassian.net/wiki' not in href and p.netloc and self.client._host not in p.netloc:
            return None
        # /pages/<id> or /spaces/.../pages/<id>
        m = re.search(r"/pages/(\d+)", href) or re.search(r"/spaces/[A-Z0-9\-_]+/pages/(\d+)", href)
        if not m:
            return None
        target = m.group(1)
        if target not in fmap:
            return None
        anchor = ''
        if '#' in href:
            anchor = '#' + href.split('#', 1)[1]
        src_dir = (self.cfg.docs_dir / page_path).parent
        dest = self.cfg.docs_dir / fmap[target]
        rel = os.path.relpath(dest, start=src_dir)
        return rel + anchor

    def to_markdown(self, html: str) -> str:
        return md(html, heading_style='ATX', strip=None)


class Writer:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    @staticmethod
    def _has_number_prefix(title: str) -> bool:
        return bool(re.match(r"^\d+(?:\.\d+)*\s+", title.strip()))

    def _compute_numbering(self, pages: Dict[str, Page], root_id: str) -> Dict[str, str]:
        """Assign numbering for two levels: N.0 for parent with children, N.i for its children.
        Leaf at top-level gets N.
        """
        nums: Dict[str, str] = {}
        top = pages.get(root_id)
        if not top:
            return nums
        for i, pid in enumerate(top.children, start=1):
            child = pages[pid]
            if child.children:
                nums[pid] = f"{i}.0"
                for j, gcid in enumerate(child.children, start=1):
                    nums[gcid] = f"{i}.{j}"
            else:
                nums[pid] = f"{i}"
        return nums

    def write_pages(self, pages: Dict[str, Page], fmap: Dict[str, Path], proc: Processor, root_id: str) -> None:
        self.cfg.docs_dir.mkdir(parents=True, exist_ok=True)
        numbering = self._compute_numbering(pages, root_id)
        for pid, page in pages.items():
            rel = fmap[pid]
            absf = self.cfg.docs_dir / rel
            absf.parent.mkdir(parents=True, exist_ok=True)
            cleaned, _ = proc.clean_html(page, page.html, rel, fmap)
            md_text = proc.to_markdown(cleaned)
            # Add/adjust H1 with numbering when appropriate
            prefix = numbering.get(pid)
            numbered_title = page.title
            if prefix and not self._has_number_prefix(page.title):
                numbered_title = f"{prefix} {page.title}"
            # Ensure first line is a single H1 reflecting numbered_title for all pages
            first_h1 = re.search(r"^#\s+.+", md_text, re.MULTILINE)
            if first_h1:
                # Replace only the first H1 occurrence
                md_text = re.sub(r"^#\s+.+", f"# {numbered_title}", md_text, count=1, flags=re.MULTILINE)
            else:
                md_text = f"# {numbered_title}\n\n" + md_text
            absf.write_text(md_text, encoding='utf-8')

        # Write a generated landing page with category boxes
        self._write_homepage(pages, fmap, root_id)

    def _write_homepage(self, pages: Dict[str, Page], fmap: Dict[str, Path], root_id: str) -> None:
        root = pages.get(root_id)
        if not root:
            return
        site_title = root.title or "Documentation"
        tiles: List[str] = []
        # Add a tile for the root section itself (e.g., "John Doe Company") if it has children
        if root.children and root_id in fmap:
            root_href = str(fmap[root_id]).replace('\\\\', '/')
            if root_href.endswith('.md'):
                root_href = root_href[:-3] + '/'
            tiles.append(
                (
                    "<a class=\"category-card\" href=\"{href}\">"
                    "  <div class=\"card-title\">{title}</div>"
                    "</a>"
                ).format(href=root_href, title=htmlesc.escape(site_title))
            )
        for cid in root.children:
            if cid not in fmap or cid not in pages:
                continue
            # Only show main sections that have their own children (hubs)
            if not pages[cid].children:
                continue
            title = pages[cid].title
            href = str(fmap[cid]).replace('\\\\', '/')
            if href.endswith('.md'):
                href = href[:-3] + '/'
            tiles.append(
                (
                    "<a class=\"category-card\" href=\"{href}\">"
                    "  <div class=\"card-title\">{title}</div>"
                    "</a>"
                ).format(href=href, title=htmlesc.escape(title))
            )
        grid_html = "\n".join(tiles)
        homepage_md = (
            f"# {site_title}\n\n"
            "Welcome. Choose a category to get started:\n\n"
            "<div class=\"category-grid\">\n"
            f"{grid_html}\n"
            "</div>\n"
        )
        (self.cfg.docs_dir / "index.md").write_text(homepage_md, encoding="utf-8")

    def _first_paragraph_text(self, html: str) -> str:
        """Extract first non-empty paragraph-like text, trimmed to ~160 chars."""
        try:
            soup = BeautifulSoup(html or "", 'html.parser')
            # Prefer paragraphs, fall back to list items or headings
            candidates = soup.find_all(['p', 'li', 'h2', 'h3'], limit=10)
            for el in candidates:
                txt = (el.get_text(" ", strip=True) or "").strip()
                if len(txt) >= 8:
                    return (txt[:157] + '…') if len(txt) > 160 else txt
        except Exception:
            pass
        return ""

    def generate_nav(self, pages: Dict[str, Page], fmap: Dict[str, Path], root_id: str) -> List:
        def item(pid: str) -> Dict:
            # Left navigation should not include numbering
            return {pages[pid].title: str(fmap[pid])}

        def build(pid: str) -> List:
            arr: List = []
            for cid in pages[pid].children:
                ch = pages[cid]
                if ch.children:
                    section = [item(cid)]
                    section.extend(build(cid))
                    arr.append({ch.title: section})
                else:
                    arr.append(item(cid))
            return arr

        nav: List = [{"Home": "index.md"}, {"Overview": str(fmap[root_id])}]
        nav.extend(build(root_id))
        return nav

    def update_mkdocs(self, site_name: str, nav: List) -> None:
        base = {
            'site_name': site_name,
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
            'docs_dir': str(self.cfg.docs_dir),
            'plugins': ['search', 'minify'],
        }
        if self.cfg.mkdocs_path.exists():
            try:
                with open(self.cfg.mkdocs_path, 'r', encoding='utf-8') as f:
                    existing = yaml.safe_load(f) or {}
                    base.update(existing)
            except Exception as e:
                logger.warning("mkdocs.yml load failed: %s", e)
        base['nav'] = nav
        with open(self.cfg.mkdocs_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(base, f, sort_keys=False, allow_unicode=True)


def slugify(text: str) -> str:
    if not text:
        return 'page'
    s = text.strip().lower()
    s = re.sub(r"[\s/]+", '-', s)
    s = re.sub(r"[^a-z0-9\-_]", '', s)
    s = re.sub(r"-+", '-', s).strip('-')
    return s or 'page'


def main() -> None:
    try:
        cfg = Config.from_env()
        client = ConfluenceClient(cfg)
        proc = Processor(cfg, client)
        writer = Writer(cfg)

        logger.info("Collecting pages...")
        pages = proc.collect(cfg.root_page_id)
        if not pages:
            logger.error('No pages collected'); sys.exit(1)
        fmap = proc.build_map(pages, cfg.root_page_id)
        logger.info("Writing markdown...")
        writer.write_pages(pages, fmap, proc, cfg.root_page_id)
        nav = writer.generate_nav(pages, fmap, cfg.root_page_id)
        writer.update_mkdocs(pages.get(cfg.root_page_id, Page('', 'Confluence Docs', [], '')).title, nav)
        s = client.stats()
        logger.info("Done. pages=%d requests=%d assets=%d", len(pages), s['requests'], len(proc.assets))
    except (ConfigError, ConfluenceError) as e:
        logger.error(str(e)); sys.exit(1)
    except KeyboardInterrupt:
        logger.info('Interrupted'); sys.exit(130)
    except Exception as e:
        logger.exception('Unexpected error: %s', e); sys.exit(1)


if __name__ == '__main__':
    main()
#!/usr/bin/env python3
"""
Confluence → MkDocs Converter (Improved)
Focus: robust HTML cleanup, error handling, and reduced complexity
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
    docs_dir: Path = field(default_factory=lambda: Path("docs"))
    mkdocs_path: Path = field(default_factory=lambda: Path("mkdocs.yml"))

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(override=False)
        
        required_vars = {
            'CONFLUENCE_BASE_URL': 'base_url',
            'CONFLUENCE_EMAIL': 'email',
            'CONFLUENCE_API_TOKEN': 'token',
            'CONFLUENCE_ROOT_PAGE_ID': 'root_page_id',
        }
        
        data = {}
        missing = []
        
        for env_var, attr_name in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                missing.append(env_var)
            else:
                data[attr_name] = value
        
        if missing:
            raise ConfigError(f"Missing environment variables: {', '.join(missing)}")
        
        # Optional overrides
        if docs_dir := os.getenv('DOCS_DIR'):
            data['docs_dir'] = Path(docs_dir)
        if mkdocs_path := os.getenv('MKDOCS_PATH'):
            data['mkdocs_path'] = Path(mkdocs_path)
        
        cfg = cls(**data)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.base_url.startswith(('http://', 'https://')):
            raise ConfigError('CONFLUENCE_BASE_URL must be an HTTP(S) URL')
        if '@' not in self.email:
            raise ConfigError('CONFLUENCE_EMAIL appears to be invalid')


@dataclass
class Page:
    id: str
    title: str
    ancestors: List[Dict[str, str]]
    html: str
    children: List[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slugify(self.title)


class ConfluenceClient:
    DEFAULT_RETRY_AFTER = 1
    MAX_RETRIES = 3
    PAGE_LIMIT = 100

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip('/')
        self.api = f"{self.base_url}/rest/api"
        self.session = requests.Session()
        self.session.auth = (cfg.email, cfg.token)
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "confluence-mkdocs-converter/1.0"
        })
        self._request_count = 0
        self._host = urlparse(self.base_url).netloc

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Execute HTTP request with retry logic."""
        for attempt in range(self.MAX_RETRIES):
            try:
                self._request_count += 1
                response = self.session.request(method, url, **kwargs)
                
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', self.DEFAULT_RETRY_AFTER))
                    time.sleep(retry_after)
                    continue
                    
                response.raise_for_status()
                return response
                
            except requests.RequestException as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise ConfluenceError(f"Request failed: {e}")
                time.sleep(2 ** (attempt + 1))
        
        raise ConfluenceError("Max retries exceeded")

    def _get_json(self, path: str, params: Optional[Dict] = None) -> dict:
        """GET request returning JSON."""
        url = path if path.startswith('http') else f"{self.api}{path}"
        return self._request('GET', url, params=params).json()

    def get_page(self, page_id: str) -> Page:
        """Fetch a single page."""
        data = self._get_json(f"/content/{page_id}", params={"expand": "body.view,ancestors"})
        
        return Page(
            id=str(data['id']),
            title=data.get('title', f"Page {page_id}"),
            ancestors=[
                {"id": str(a['id']), "title": a.get('title', str(a['id']))} 
                for a in data.get('ancestors', [])
            ],
            html=data.get('body', {}).get('view', {}).get('value', ''),
        )

    def list_children(self, page_id: str) -> List[Page]:
        """List all child pages."""
        children = []
        start = 0
        
        while True:
            data = self._get_json(
                f"/content/{page_id}/child/page",
                params={"limit": self.PAGE_LIMIT, "start": start, "expand": "body.view,ancestors"}
            )
            
            for item in data.get('results', []):
                children.append(Page(
                    id=str(item['id']),
                    title=item.get('title', str(item['id'])),
                    ancestors=[
                        {"id": str(a['id']), "title": a.get('title', str(a['id']))} 
                        for a in item.get('ancestors', [])
                    ],
                    html=item.get('body', {}).get('view', {}).get('value', ''),
                ))
            
            if data.get('_links', {}).get('next'):
                start += self.PAGE_LIMIT
            else:
                break
        
        return children

    def download_file(self, url: str, dest: Path) -> None:
        """Download a file from Confluence."""
        if url.startswith('/'):
            url = f"{self.base_url}{url}"
        
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        with self._request('GET', url, stream=True) as response:
            with open(dest, 'wb') as f:
                shutil.copyfileobj(response.raw, f)

    def is_confluence_asset(self, url: str) -> bool:
        """Check if URL points to a Confluence asset."""
        parsed = urlparse(url)
        
        if not parsed.netloc:
            return parsed.path.startswith(('/wiki/', '/download/'))
        
        return (self._host in parsed.netloc) and \
               parsed.path.startswith(('/', '/wiki/', '/download/'))

    def stats(self) -> Dict[str, int]:
        """Return statistics."""
        return {"requests": self._request_count}


class Processor:
    def __init__(self, cfg: Config, client: ConfluenceClient):
        self.cfg = cfg
        self.client = client
        self.downloaded_assets: Set[str] = set()

    def collect_pages(self, root_id: str) -> Dict[str, Page]:
        """Collect all pages starting from root."""
        pages = {}
        visited = set()
        
        def traverse(page_id: str, depth: int = 0):
            if page_id in visited:
                return
            
            visited.add(page_id)
            logger.info("%sFetching page %s", "  " * depth, page_id)
            
            try:
                page = self.client.get_page(page_id)
                pages[page_id] = page
                
                children = self.client.list_children(page_id)
                page.children = [child.id for child in children]
                
                for child in children:
                    pages[child.id] = child
                    traverse(child.id, depth + 1)
                    
            except Exception as e:
                logger.error("Failed to fetch page %s: %s", page_id, e)
        
        traverse(root_id)
        return pages

    def build_file_map(self, pages: Dict[str, Page], root_id: str) -> Dict[str, Path]:
        """Build mapping from page IDs to file paths."""
        file_map = {}
        
        for page_id, page in pages.items():
            path_segments = []
            after_root = False
            
            for ancestor in page.ancestors:
                if str(ancestor['id']) == str(root_id):
                    after_root = True
                    continue
                if after_root:
                    path_segments.append(slugify(ancestor['title']))
            
            filename = 'overview.md' if page_id == str(root_id) else f"{page.slug}-{page_id}.md"
            file_map[page_id] = Path(*path_segments, filename) if path_segments else Path(filename)
        
        return file_map

    def clean_html(self, page: Page, html: str, page_path: Path, file_map: Dict[str, Path]) -> Tuple[str, List[Path]]:
        """Clean and process HTML content."""
        if not html:
            return "", []
        
        soup = BeautifulSoup(html, 'html.parser')
        
        # Normalize headings
        self._normalize_headings(soup)
        
        # Remove inline styles
        for tag in soup.find_all(True):
            if 'style' in tag.attrs:
                del tag['style']
        
        # Process images
        downloaded_files = []
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and self.client.is_confluence_asset(src):
                new_src, asset_path = self._process_asset(page, src, page_path)
                if new_src:
                    img['src'] = new_src
                if asset_path:
                    downloaded_files.append(asset_path)
        
        # Process links
        for link in soup.find_all('a'):
            href = link.get('href')
            if href:
                new_href = self._rewrite_link(href, page_path, file_map)
                if new_href:
                    link['href'] = new_href
        
        return str(soup), downloaded_files

    def _normalize_headings(self, soup: BeautifulSoup) -> None:
        """Convert multiple h1s to h2s (keep only first h1)."""
        h1_tags = soup.find_all('h1')
        for i, h1 in enumerate(h1_tags):
            if i > 0 and isinstance(h1, Tag):
                h1.name = 'h2'

    def _process_asset(self, page: Page, src: str, page_path: Path) -> Tuple[Optional[str], Optional[Path]]:
        """Download and process an asset."""
        try:
            # Extract filename from URL
            clean_url = src.split('?', 1)[0]
            filename = unquote(os.path.basename(clean_url)) or 'asset'
            
            # Create asset path
            asset_rel_path = Path('assets') / page.id / filename
            asset_full_path = self.cfg.docs_dir / asset_rel_path
            
            # Download if not already downloaded
            asset_key = str(asset_rel_path)
            if asset_key not in self.downloaded_assets:
                self.client.download_file(src, asset_full_path)
                self.downloaded_assets.add(asset_key)
            
            # Calculate relative path from page to asset
            page_dir = (self.cfg.docs_dir / page_path).parent
            relative_src = os.path.relpath(asset_full_path, start=page_dir)
            
            return relative_src, asset_rel_path
            
        except Exception as e:
            logger.warning("Failed to download asset %s: %s", src, e)
            return None, None

    def _rewrite_link(self, href: str, page_path: Path, file_map: Dict[str, Path]) -> Optional[str]:
        """Rewrite internal Confluence links to markdown links."""
        # Skip anchors and mailto links
        if href.startswith(('#', 'mailto:')):
            return None
        
        # Check if it's an external link (not Confluence)
        parsed = urlparse(href)
        if parsed.scheme and 'atlassian.net/wiki' not in href:
            if parsed.netloc and self.client._host not in parsed.netloc:
                return None
        
        # Extract page ID from Confluence URLs
        match = re.search(r"/pages/(\d+)", href)
        if not match:
            return None
        
        target_id = match.group(1)
        if target_id not in file_map:
            logger.debug("Link target %s not found in file map", target_id)
            return None
        
        # Preserve anchors
        anchor = ''
        if '#' in href:
            anchor = '#' + href.split('#', 1)[1]
        
        # Calculate relative path
        source_dir = (self.cfg.docs_dir / page_path).parent
        target_path = self.cfg.docs_dir / file_map[target_id]
        relative_path = os.path.relpath(target_path, start=source_dir)
        
        return relative_path + anchor

    def to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        if not html:
            return ""
        return md(html, heading_style='ATX', strip=None)


class Writer:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def write_pages(self, pages: Dict[str, Page], file_map: Dict[str, Path], processor: Processor, root_id: str) -> None:
        """Write all pages to markdown files."""
        self.cfg.docs_dir.mkdir(parents=True, exist_ok=True)
        
        numbering = self._compute_numbering(pages, root_id)
        
        for page_id, page in pages.items():
            rel_path = file_map[page_id]
            abs_path = self.cfg.docs_dir / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Clean HTML and convert to markdown
            cleaned_html, _ = processor.clean_html(page, page.html, rel_path, file_map)
            md_content = processor.to_markdown(cleaned_html)
            
            # Add numbering if needed
            title = self._get_numbered_title(page, numbering.get(page_id))
            
            # Ensure H1 title
            md_content = self._ensure_h1_title(md_content, title)
            
            abs_path.write_text(md_content, encoding='utf-8')
        
        # Write homepage
        self._write_homepage(pages, file_map, root_id)

    def _compute_numbering(self, pages: Dict[str, Page], root_id: str) -> Dict[str, str]:
        """Compute section numbering."""
        numbering = {}
        root = pages.get(root_id)
        if not root:
            return numbering
        
        for i, child_id in enumerate(root.children, start=1):
            child = pages.get(child_id)
            if not child:
                continue
            
            if child.children:
                numbering[child_id] = f"{i}.0"
                for j, grandchild_id in enumerate(child.children, start=1):
                    numbering[grandchild_id] = f"{i}.{j}"
            else:
                numbering[child_id] = str(i)
        
        return numbering

    def _get_numbered_title(self, page: Page, number: Optional[str]) -> str:
        """Get title with optional numbering."""
        if number and not re.match(r"^\d+(?:\.\d+)*\s+", page.title.strip()):
            return f"{number} {page.title}"
        return page.title

    def _ensure_h1_title(self, md_content: str, title: str) -> str:
        """Ensure markdown starts with H1 title."""
        # Check for existing H1
        if re.match(r"^#\s+", md_content):
            # Replace first H1
            return re.sub(r"^#\s+.+", f"# {title}", md_content, count=1, flags=re.MULTILINE)
        else:
            # Add H1
            return f"# {title}\n\n{md_content}"

    def _write_homepage(self, pages: Dict[str, Page], file_map: Dict[str, Path], root_id: str) -> None:
        """Write landing page with category cards."""
        root = pages.get(root_id)
        if not root:
            return
        
        site_title = root.title or "Documentation"
        cards = []
        
        # Add card for root section if it has content
        if root.children and root_id in file_map:
            href = str(file_map[root_id]).replace('\\', '/').replace('.md', '/')
            cards.append(self._create_card(site_title, href))
        
        # Add cards for main sections with children
        for child_id in root.children:
            if child_id not in file_map or child_id not in pages:
                continue
            
            child = pages[child_id]
            if not child.children:
                continue
            
            href = str(file_map[child_id]).replace('\\', '/').replace('.md', '/')
            cards.append(self._create_card(child.title, href))
        
        homepage_content = f"""# {site_title}

Welcome. Choose a category to get started:

<div class="category-grid">
{chr(10).join(cards)}
</div>
"""
        
        (self.cfg.docs_dir / "index.md").write_text(homepage_content, encoding="utf-8")

    def _create_card(self, title: str, href: str) -> str:
        """Create HTML for a category card."""
        return (
            f'<a class="category-card" href="{href}">\n'
            f'  <div class="card-title">{htmlesc.escape(title)}</div>\n'
            f'</a>'
        )

    def generate_nav(self, pages: Dict[str, Page], file_map: Dict[str, Path], root_id: str) -> List:
        """Generate navigation structure for MkDocs."""
        def build_nav_item(page_id: str) -> Dict[str, str]:
            return {pages[page_id].title: str(file_map[page_id])}
        
        def build_nav_tree(page_id: str) -> List:
            nav_items = []
            page = pages.get(page_id)
            if not page:
                return nav_items
            
            for child_id in page.children:
                child = pages.get(child_id)
                if not child:
                    continue
                
                if child.children:
                    # Section with subsections
                    section = [build_nav_item(child_id)]
                    section.extend(build_nav_tree(child_id))
                    nav_items.append({child.title: section})
                else:
                    # Leaf page
                    nav_items.append(build_nav_item(child_id))
            
            return nav_items
        
        nav = [
            {"Home": "index.md"},
            {"Overview": str(file_map[root_id])}
        ]
        nav.extend(build_nav_tree(root_id))
        
        return nav

    def update_mkdocs(self, site_name: str, nav: List) -> None:
        """Update or create mkdocs.yml configuration."""
        config = {
            'site_name': site_name,
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
            'docs_dir': str(self.cfg.docs_dir),
            'plugins': ['search', 'minify'],
            'nav': nav
        }
        
        # Merge with existing config if present
        if self.cfg.mkdocs_path.exists():
            try:
                with open(self.cfg.mkdocs_path, 'r', encoding='utf-8') as f:
                    existing = yaml.safe_load(f) or {}
                    # Preserve existing config, update nav
                    existing.update(config)
                    config = existing
            except Exception as e:
                logger.warning("Could not load existing mkdocs.yml: %s", e)
        
        with open(self.cfg.mkdocs_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    if not text:
        return 'page'
    
    slug = text.strip().lower()
    slug = re.sub(r"[\s/]+", '-', slug)
    slug = re.sub(r"[^a-z0-9\-_]", '', slug)
    slug = re.sub(r"-+", '-', slug).strip('-')
    
    return slug or 'page'


def main() -> None:
    """Main entry point."""
    try:
        # Load configuration
        config = Config.from_env()
        
        # Initialize components
        client = ConfluenceClient(config)
        processor = Processor(config, client)
        writer = Writer(config)
        
        # Collect pages
        logger.info("Collecting pages from Confluence...")
        pages = processor.collect_pages(config.root_page_id)
        
        if not pages:
            logger.error("No pages collected")
            sys.exit(1)
        
        # Build file mapping
        file_map = processor.build_file_map(pages, config.root_page_id)
        
        # Write markdown files
        logger.info("Writing markdown files...")
        writer.write_pages(pages, file_map, processor, config.root_page_id)
        
        # Generate navigation and update MkDocs config
        nav = writer.generate_nav(pages, file_map, config.root_page_id)
        root_page = pages.get(config.root_page_id, Page('', 'Documentation', [], ''))
        writer.update_mkdocs(root_page.title, nav)
        
        # Print statistics
        stats = client.stats()
        logger.info(
            "Conversion complete: %d pages, %d API requests, %d assets downloaded",
            len(pages), stats['requests'], len(processor.downloaded_assets)
        )
        
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)
    except ConfluenceError as e:
        logger.error("Confluence API error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        sys.exit(1)


if __name__ == '__main__':
    main()
