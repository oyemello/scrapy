import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.sync_confluence_vSept15_v2 import (
    Config,
    InlineWriter,
    Page,
    slugify,
    strip_first_h1,
)


class SlugifyTests(unittest.TestCase):
    def test_slugify_handles_whitespace_and_case(self) -> None:
        self.assertEqual(slugify(' Hello World '), 'hello-world')
        self.assertEqual(slugify('Docs/Overview'), 'docs-overview')

    def test_slugify_fallback(self) -> None:
        self.assertEqual(slugify('!!!'), 'page')
        self.assertEqual(slugify(''), 'page')


class MarkdownHelpersTests(unittest.TestCase):
    def test_strip_first_h1(self) -> None:
        text = '# Title\n\nContent\n## Section\n'
        self.assertEqual(strip_first_h1(text), 'Content\n## Section\n')


class InlineWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config(
            base_url='https://example.atlassian.net/wiki',
            email='user@example.com',
            token='token',
            root_page_id='1',
        )
        self.cfg.out_dir = Path('build')
        self.pages = {
            '1': Page('1', 'Root', [], '<p>root</p>'),
            '2': Page('2', 'Child', [], '<p>child</p>'),
        }
        dummy_client = SimpleNamespace(resolve_page_id=lambda href: {'https://example.atlassian.net/wiki/pages/2': '2'}.get(href))
        dummy_proc = SimpleNamespace(client=dummy_client, clean_html=lambda page, html, rel: html)
        self.writer = InlineWriter(self.cfg, dummy_proc, self.pages, '1')
        self.writer.file_map = {
            '1': Path('overview.md'),
            '2': Path('child-2.md'),
        }

    def test_rel_href_generates_directory_style_links(self) -> None:
        href = self.writer._rel_href(Path('child-2.md'), Path('overview.md'))
        self.assertEqual(href, '../overview/')
        href = self.writer._rel_href(Path('overview.md'), Path('child-2.md'))
        self.assertEqual(href, '../child-2/')

    def test_rewrite_links_for_file_maps_confluence_pages(self) -> None:
        md_text = '[Child](https://example.atlassian.net/wiki/pages/2)'
        rewritten = self.writer._rewrite_links_for_file(md_text, Path('overview.md'))
        self.assertIn('[Child](child-2.md)', rewritten)


if __name__ == '__main__':
    unittest.main()
