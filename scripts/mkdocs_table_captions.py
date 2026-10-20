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

Numbers are deliberately not reproduced here: nothing else on the website
is numbered either (headings render unnumbered in the nav/page body --
`--number-sections` is a PDF-only concern, see CLAUDE.md's Architecture
section), and Pandoc's own table numbering is a side effect of LaTeX's
float counter, not a value that exists anywhere to read back out of
docs/*.md.

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
import json
import logging
import re
from pathlib import Path

from mkdocs.structure.files import File
from mkdocs.structure.nav import Link

ROOT = Path(__file__).resolve().parent.parent
CAPTION_RE = re.compile(r"(?m)^: (.+)$")
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


def on_page_markdown(markdown, **kwargs):
    return CAPTION_RE.sub(r'<p class="table-caption">\1</p>', markdown)


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
    if files.get_file_from_path(PDF_FILE.name):
        # A site-root-relative URL: the theme's `url` filter rewrites it
        # relative to each page, so it resolves from any page depth and
        # under any deployment subpath (e.g. a GitHub Pages project site).
        nav.items.append(Link(PDF_NAV_TITLE, PDF_FILE.name))
    return nav
