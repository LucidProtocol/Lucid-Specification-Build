"""MkDocs hook: render Pandoc's `: Caption text` table-caption syntax as a
styled paragraph instead of literal text.

docs/*.md tables are captioned using Pandoc's own convention -- a line
reading `: Caption text` immediately before or after the table -- because
build_pdf.py reads docs/ with Pandoc's `markdown` format, and that's the
one syntax it recognizes as a numbered, cross-referenceable table caption
(`--number-sections` renders it as "Table N: Caption text", matching the
"see Table X" cross-references already in the body text). But MkDocs'
own Markdown engine (Python-Markdown's `tables` extension) has never heard
of this syntax -- it's plain Pandoc markdown, not CommonMark/GFM -- so
without this hook every caption shows up on the website as a literal
stray paragraph starting with a colon.

The website also gets the PDF's numbering: section numbers on every
heading, in the "on this page" contents and in the nav, and "Table N:"/
"Figure N:" on captions (Python-Markdown shows no caption under an image
at all, so figures get one added). MkDocs numbers nothing itself, and in
the PDF the numbers come from LaTeX's counters, not from anything in
docs/*.md, so on_nav counts them the way LaTeX does: every page in nav
order, skipping the cover page (build_pdf.py replaces it with raw LaTeX),
headings marked unnumbered (the same UNNUMBERED_HEADING_CLASS rule
build_pdf.py applies), top-level headings lettered A, B, ... from
FIRST_APPENDIX_FILE on (LaTeX's \\appendix), one running count of
captioned tables, and one of figures (an image alone in its paragraph
with alt text, which Pandoc turns into a captioned figure). Numbers are
added to the rendered HTML, never to the Markdown, so heading anchors --
and every link to them -- stay exactly as they were.

This only affects the MkDocs build. build_pdf.py reads docs/*.md
directly and never runs MkDocs hooks, so the PDF still sees the real
`: Caption text` line and numbers it as before. GitHub's own file preview
doesn't run this hook either -- a raw `: Caption text` line is harmless
there (still reads fine as plain text), just not specially styled.

on_config below is an unrelated second hook living in this same file
(mkdocs.yml's hooks: list is what's plural, not this module) -- it stamps
the built site's footer with which Lucid-Specification commit it was
built from, the same provenance build_pdf.py writes into the PDF's own
footer/dist/build-info.json, via Material's native `copyright:` config
value rather than a custom theme override.

on_files/on_nav are a third: if build_pdf.py has already produced
dist/lucid-protocol-specification.pdf, they copy it into the built site
and add a "Download PDF" link at the end of the nav. The link lives here
rather than in mkdocs.yml's nav: for two reasons -- a nav: entry for a
file that isn't in docs_dir fails `mkdocs build --strict`, and build_pdf.py
reads that same nav: to decide what goes into the PDF. Doing it here also
means the link only appears when there's a PDF for it to point at, so
building the site without the PDF never produces a broken link. Build
the PDF first to get it. The PDF is also skipped, with a warning, unless
dist/build-info.json shows it was built from the same spec commit as
spec-version.json, so the site never links a PDF of a different spec
version (under --strict that warning fails the build). on_files also
copies this repo's assets/favicon.png and favicon.ico to the site root.
"""
import html
import json
import logging
import re
import sys
from pathlib import Path

from mkdocs.structure.files import File
from mkdocs.structure.nav import Link, Section
from mkdocs.structure.pages import Page

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_pdf  # noqa: E402 -- shares the PDF build's numbering rules

ROOT = Path(__file__).resolve().parent.parent
CAPTION_RE = re.compile(r"(?m)^: (.+)$")
EMPTY_TBODY_RE = re.compile(r"\s*<tbody>\s*<tr>\s*(?:<td[^>]*>\s*</td>\s*)+</tr>\s*</tbody>")
SPEC_VERSION_FILE = ROOT / "spec-version.json"
PDF_FILE = ROOT / "dist" / "lucid-protocol-specification.pdf"
PDF_BUILD_INFO_FILE = ROOT / "dist" / "build-info.json"
PDF_NAV_TITLE = "Download PDF"
# Copied to the site root as they are. favicon.png is what mkdocs.yml's
# theme.favicon points pages at; favicon.ico is for whatever has no page
# to point it -- e.g. the browser's PDF viewer, which asks for
# /favicon.ico and otherwise gets a 404.
SITE_ROOT_ASSETS = [ROOT / "assets" / "favicon.png", ROOT / "assets" / "favicon.ico"]
log = logging.getLogger("mkdocs.hooks.pdf_link")

# Numbering (see the module docstring).
FENCE_RE = re.compile(r"^\s*(```|~~~)")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
MD_HEADING_ATTRS_RE = re.compile(r"\{([^}]*)\}$")
MD_FIGURE_RE = re.compile(r"^!\[[^\]]+\]\([^)]*\)(\{[^}]*\})?$")
HTML_HEADING_RE = re.compile(r"<h([1-6])\b([^>]*)>")
HTML_ID_RE = re.compile(r'\bid="([^"]+)"')
HTML_FIGURE_RE = re.compile(r'<p>(<img\b[^>]*\balt="([^"]+)"[^>]*>)</p>')
# Already carry their letter in mkdocs.yml's nav ("Appendix A: ...").
LETTERED_NAV_TITLE_PREFIX = "Appendix"
num_log = logging.getLogger("mkdocs.hooks.numbering")

_numbers = {}  # src_uri -> {"headings": [(level, number or None)], "tables": [n], "figures": [n]}
_page_titles = {}  # src_uri -> nav title before numbering


class _Counters:
    """LaTeX's sectioning/table/figure counters, run over the whole spec."""

    def __init__(self):
        self.sections = [0] * 6
        self.appendix = False
        self.tables = 0
        self.figures = 0

    def start_appendix(self):
        self.appendix = True
        self.sections = [0] * 6

    def heading(self, level, attrs):
        classes = attrs.split()
        if ".unnumbered" in classes or "-" in classes or build_pdf.UNNUMBERED_HEADING_CLASS in classes:
            return None
        self.sections[level - 1] += 1
        self.sections[level:] = [0] * (6 - level)
        parts = [str(n) for n in self.sections[:level]]
        if self.appendix:
            parts[0] = chr(ord("A") + self.sections[0] - 1)
        return ".".join(parts)


def _scan(markdown, counters):
    headings, figures = [], []
    lines = markdown.splitlines()
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = MD_HEADING_RE.match(line)
        if m:
            attrs = MD_HEADING_ATTRS_RE.search(m.group(2))
            level = len(m.group(1))
            headings.append((level, counters.heading(level, attrs.group(1) if attrs else "")))
        elif MD_FIGURE_RE.match(line.strip()) and (i == 0 or not lines[i - 1].strip()) and (
            i + 1 == len(lines) or not lines[i + 1].strip()
        ):
            counters.figures += 1
            figures.append(counters.figures)
    # Counted the same way on_page_markdown finds them, so the two agree.
    tables = []
    for _ in CAPTION_RE.finditer(markdown):
        counters.tables += 1
        tables.append(counters.tables)
    return {"headings": headings, "tables": tables, "figures": figures}


def _pages(items):
    for item in items:
        if isinstance(item, Page):
            yield item
        elif isinstance(item, Section):
            yield from _pages(item.children)


def _number_nav(items, section_number=None):
    for item in items:
        if isinstance(item, Section):
            # A section takes the number of its one top-level heading
            # ("Data model" -> 2); with several (e.g. "Appendices") it
            # doesn't stand for a single numbered section.
            h1s = [
                n for p in _pages(item.children)
                for level, n in _numbers.get(p.file.src_uri, {}).get("headings", [])
                if level == 1 and n
            ]
            number = h1s[0] if len(h1s) == 1 else None
            if number and not item.title.startswith(LETTERED_NAV_TITLE_PREFIX):
                item.title = f"{number} {item.title}"
            _number_nav(item.children, number)
        elif isinstance(item, Page):
            _page_titles[item.file.src_uri] = item.title
            # A page takes its first numbered heading -- skipping one it
            # only shares with its section (the "Data model" heading that
            # opens "Field device overview").
            numbers = [
                n for _, n in _numbers.get(item.file.src_uri, {}).get("headings", [])
                if n and n != section_number
            ]
            if numbers and not item.title.startswith(LETTERED_NAV_TITLE_PREFIX):
                item.title = f"{numbers[0]} {item.title}"


def _number_toc(items, numbers_by_id):
    for item in items:
        if item.id in numbers_by_id:
            item.title = f"{numbers_by_id[item.id]} {item.title}"
        _number_toc(item.children, numbers_by_id)


def on_page_markdown(markdown, page, **kwargs):
    # Same swap build_pdf.py's normalize_paths makes for the PDF: the 🛈
    # (U+1F6C8) marking the spec's note callouts is missing from most
    # fonts, so browsers show a tiny fallback glyph rather than a label.
    markdown = markdown.replace("\U0001F6C8", "**NOTE:**")
    table_numbers = iter(_numbers.get(page.file.src_uri, {}).get("tables", []))

    def caption(m):
        n = next(table_numbers, None)
        label = f"Table {n}: " if n else ""
        return f'<p class="table-caption">{label}{m.group(1)}</p>'

    return CAPTION_RE.sub(caption, markdown)


def on_page_content(html_content, page, **kwargs):
    # The spec's callout boxes (NOTE:/NOTICE/DISCLAIMER) are one-row pipe
    # tables: a header row and separator with no body rows. Pandoc renders
    # that as the one-row table it is, but Python-Markdown's tables
    # extension always emits a body row, empty if there's nothing to put in
    # it -- a blank second row under every callout. Drop it.
    html_content = EMPTY_TBODY_RE.sub("", html_content)

    src = page.file.src_uri
    expected = _numbers.get(src)
    if expected is None:
        return html_content

    headings = iter(expected["headings"])
    numbers_by_id = {}
    found_headings = 0

    def number_heading(m):
        nonlocal found_headings
        found_headings += 1
        _, number = next(headings, (None, None))
        if not number:
            return m.group(0)
        heading_id = HTML_ID_RE.search(m.group(2))
        if heading_id:
            numbers_by_id[heading_id.group(1)] = number
        return f"{m.group(0)}{number} "

    html_content = HTML_HEADING_RE.sub(number_heading, html_content)
    _number_toc(page.toc, numbers_by_id)

    figures = iter(expected["figures"])
    found_figures = 0

    def caption_figure(m):
        nonlocal found_figures
        found_figures += 1
        n = next(figures, None)
        if n is None:
            return m.group(0)
        return f'{m.group(0)}\n<p class="table-caption figure-caption">Figure {n}: {m.group(2)}</p>'

    html_content = HTML_FIGURE_RE.sub(caption_figure, html_content)

    # The counts come from scanning the Markdown; if the HTML disagrees,
    # numbers on this page could be shifted -- say so rather than guess.
    if found_headings != len(expected["headings"]) or found_figures != len(expected["figures"]):
        num_log.warning(
            "%s: expected %d headings/%d figures, found %d/%d in the HTML; "
            "its section/figure numbers may not match the PDF.",
            src, len(expected["headings"]), len(expected["figures"]), found_headings, found_figures,
        )

    # With no <h1> in the content, Material adds one from page.title --
    # which on_nav has numbered. Supply the unnumbered title instead, so a
    # page whose first heading is, say, "2.1.2 Device profile" doesn't
    # open with "2.1.2 Device profile" twice.
    if "<h1" not in html_content and src in _page_titles:
        html_content = f"<h1>{html.escape(_page_titles[src])}</h1>\n{html_content}"
    return html_content


def on_config(config, **kwargs):
    if not SPEC_VERSION_FILE.exists():
        return config
    info = json.loads(SPEC_VERSION_FILE.read_text(encoding="utf-8"))
    label = info["tag"] or info["commit_short"]
    config["copyright"] = f"Built from Lucid-Specification @ {label}"
    return config


def _pdf_matches_spec():
    """True if dist/build-info.json says the PDF was built from the same
    spec commit spec-version.json says is checked out now. Guards against
    linking a stale PDF -- e.g. after fetching newer spec content and
    rebuilding only the site."""
    try:
        built = json.loads(PDF_BUILD_INFO_FILE.read_text(encoding="utf-8"))["commit"]
        fetched = json.loads(SPEC_VERSION_FILE.read_text(encoding="utf-8"))["commit"]
    except (OSError, ValueError, KeyError):
        log.warning(
            "Not linking %s: can't confirm which spec commit it was built "
            "from (%s or %s missing). Rebuild it with build_pdf.py.",
            PDF_FILE.name, PDF_BUILD_INFO_FILE.name, SPEC_VERSION_FILE.name,
        )
        return False
    if built != fetched:
        log.warning(
            "Not linking %s: it was built from spec %s, but spec %s is "
            "checked out now. Rebuild it with build_pdf.py.",
            PDF_FILE.name, built[:7], fetched[:7],
        )
        return False
    return True


def on_files(files, config, **kwargs):
    extra = list(SITE_ROOT_ASSETS)
    if PDF_FILE.exists() and _pdf_matches_spec():
        extra.append(PDF_FILE)
    for path in extra:
        files.append(File(
            path.name, str(path.parent), config["site_dir"],
            config["use_directory_urls"],
        ))
    return files


def on_nav(nav, files, **kwargs):
    # Count every page in nav order -- the order build_pdf.py concatenates
    # them in -- before any page is rendered (MkDocs renders them in file
    # order, not nav order).
    _numbers.clear()
    _page_titles.clear()
    counters = _Counters()
    for page in nav.pages:
        src = page.file.src_uri
        if src == build_pdf.COVER_FILE:
            continue
        if src == build_pdf.FIRST_APPENDIX_FILE:
            counters.start_appendix()
        _numbers[src] = _scan(Path(page.file.abs_src_path).read_text(encoding="utf-8"), counters)
    _number_nav(nav.items)

    if files.get_file_from_path(PDF_FILE.name):
        # A site-root-relative URL: the theme's `url` filter rewrites it
        # relative to each page, so it resolves from any page depth and
        # under any deployment subpath (e.g. a GitHub Pages project site).
        nav.items.append(Link(PDF_NAV_TITLE, PDF_FILE.name))
    return nav
