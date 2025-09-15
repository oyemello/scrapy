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
    max_link_depth: int = 2

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
        self.session.headers.update({"Accept": "application/json", "User-Agent": "scrapy-vsept15-v2"})
        self._count = 0
        self._host = urlparse(self.base_url).netloc
        self._resolve_cache: Dict[str, Optional[str]] = {}

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
                page = self.client.get_page(pid)
                pages[pid] = page
                children = self.client.list_children(pid)
                page.children = [c.id for c in children]
                for ch in children:
                    if ch.id not in visited:
                        pages[ch.id] = ch
                        q.append((ch.id, ldepth + 1))
                if self.cfg.follow_links and ldepth < self.cfg.max_link_depth:
                    for linked in self._extract_linked_page_ids(page.html):
                        if linked not in visited:
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
            src = img.get('src')
            if not src:
                continue
            if self.client.is_confluence_asset(src):
                new_src, asset_rel = self._download_asset(page, src, page_path)
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
    def __init__(self, cfg: Config, proc: Processor, pages: Dict[str, Page]):
        self.cfg = cfg
        self.proc = proc
        self.pages = pages
        self.file_map: Dict[str, Path] = {}  # page id -> output path (for top-level pages only)
        self.inlined_ids_by_file: Dict[Path, Set[str]] = {}

    def build_layout(self, root_id: str) -> None:
        # Top-level pages: root + its direct children each get their own file
        self.file_map[root_id] = Path('overview.md')
        root = self.pages.get(root_id)
        if root:
            for cid in root.children:
                if cid in self.pages:
                    self.file_map[cid] = Path(f"{self.pages[cid].slug}-{cid}.md")

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

        # Compute inlined sets per output file
        for top_pid, path in self.file_map.items():
            inlined = self._collect_inlined(top_pid, self.cfg.max_link_depth)
            self.inlined_ids_by_file[path] = inlined

        # Render pages
        for top_pid, rel in self.file_map.items():
            self._write_page_with_inline(top_pid, rel)

        # Write homepage
        self._write_homepage(root_id)

        # Compose and write mkdocs config into the out_dir
        self._write_mkdocs_config(root_id)

    def _to_md(self, page: Page, rel_path: Path) -> str:
        cleaned, _ = self.proc.clean_html(page, page.html, rel_path)
        return md(cleaned or '', heading_style='ATX', strip=None)

    def _write_page_with_inline(self, pid: str, rel: Path) -> None:
        page = self.pages[pid]
        md_text = self._to_md(page, rel)
        # Ensure single H1 with the page title
        if re.search(r"^#\s+", md_text, flags=re.MULTILINE):
            md_text = re.sub(r"^#\s+.+", f"# {page.title}", md_text, count=1, flags=re.MULTILINE)
        else:
            md_text = f"# {page.title}\n\n" + md_text

        # Append children and grandchildren sections up to max depth
        def rec_children(cur: str, depth: int) -> str:
            if depth >= self.cfg.max_link_depth:
                return ''
            out = []
            for cid in self.pages.get(cur, Page('', '', [], '')).children:
                ch = self.pages[cid]
                level = min(6, depth + 2)  # child sections start at H2
                # Use attr_list anchor so MkDocs recognizes anchor in nav
                heading = f"{'#' * level} {ch.title} {{#sec-{cid}}}\n\n"
                ch_md = self._to_md(ch, rel)
                ch_md = strip_first_h1(ch_md)
                ch_md = shift_headings(ch_md, depth + 1)  # push nested headings down
                out.append(heading + ch_md + '\n')
                out.append(rec_children(cid, depth + 1))
            return ''.join(out)

        md_text = md_text.rstrip() + '\n\n' + rec_children(pid, 0)

        (self.cfg.out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        # Rewrite links to local anchors or pages
        md_text = self._rewrite_links_for_file(md_text, rel)
        (self.cfg.out_dir / rel).write_text(md_text, encoding='utf-8')

    def _rewrite_links_for_file(self, md_text: str, rel: Path) -> str:
        inlined = self.inlined_ids_by_file.get(rel, set())
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
            # Rewrite if inlined in this file
            if pid in inlined:
                return f"[{text}](#sec-{pid})"
            # Else if pid is a top-level page, link to its file
            if pid in id_to_file:
                return f"[{text}]({id_to_file[pid].as_posix()})"
            return m.group(0)

        pattern = re.compile(r"(?P<bang>!?)\[(?P<text>[^\]]*)\]\((?P<href>[^)]+)\)")
        return pattern.sub(repl, md_text)

    def _write_homepage(self, root_id: str) -> None:
        root = self.pages.get(root_id)
        title = (root.title if root else 'Documentation')
        tiles: List[str] = []
        if root:
            for cid in root.children:
                if cid not in self.pages:
                    continue
                href = f"{self.pages[cid].slug}-{cid}.md"
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

        # Build nav: Home, Overview, and one entry per top-level child.
        nav: List = [{"Home": 'index.md'}]
        nav.append({"Overview": 'overview.md'})
        root = self.pages.get(root_id)
        if root:
            for cid in root.children:
                page = self.pages.get(cid)
                if not page:
                    continue
                top_path = f"{page.slug}-{cid}.md"
                entry: Dict = {page.title: top_path}
                if page.children:
                    sub: List = [entry]
                    for gcid in page.children:
                        if gcid in self.pages:
                            sub.append({self.pages[gcid].title: f"{top_path}#sec-{gcid}"})
                    nav.append({page.title: sub})
                else:
                    nav.append(entry)
        base['nav'] = nav

        # Ensure attr_list extension is enabled for explicit heading IDs
        mdext = base.get('markdown_extensions', []) or []
        if isinstance(mdext, dict):
            mdext = [mdext]
        if 'attr_list' not in mdext:
            mdext.append('attr_list')
        base['markdown_extensions'] = mdext

        with open(self.cfg.out_dir / 'mkdocs.yml', 'w', encoding='utf-8') as f:
            yaml.safe_dump(base, f, sort_keys=False, allow_unicode=True)


def run_mkdocs(out_dir: Path, deploy: bool = True) -> None:
    cfg_path = out_dir / 'mkdocs.yml'
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

        writer = InlineWriter(cfg, proc, pages)
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
