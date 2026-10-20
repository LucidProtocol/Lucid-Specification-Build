#!/usr/bin/env python3
"""
Build a single PDF from the fetched Lucid Specification content
(lucid-specification/docs/, populated by fetch_spec.py -- run that first),
in the same order as the website's nav.

mkdocs.yml's `nav:` is the one place page order/grouping is defined; this
script reads it (rather than re-listing files here) so the site and the
PDF can never drift out of sync with each other.
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# lucid-specification/ is fetched from the public Lucid-Specification repo
# by fetch_spec.py, not carried in this repo -- see that script's docstring.
DOCS = ROOT / "lucid-specification" / "docs"
SPEC_VERSION_FILE = ROOT / "spec-version.json"
MKDOCS_YML = ROOT / "mkdocs.yml"
OUT_DIR = ROOT / "dist"
OUT_PDF = OUT_DIR / "lucid-protocol-specification.pdf"
OUT_BUILD_INFO = OUT_DIR / "build-info.json"
BUILD = ROOT / "build" / "pdf"
COMBINED_MD = BUILD / "combined.md"
# Pandoc normally goes straight from Markdown to a finished PDF in one
# call (`-o *.pdf`), driving xelatex internally. add_table_grid_lines
# needs to intervene on the LaTeX Pandoc generates for every table
# *before* xelatex ever sees it (Pandoc's LaTeX table writer has no
# option for full grid lines -- see that function), so the build instead
# stops at this intermediate .tex file, patches it, and this script
# drives xelatex itself from there (see run_xelatex).
COMBINED_TEX = BUILD / "combined.tex"
RASTER_DIR = BUILD / "assets" / "images"
# Pandoc's default highlight style ("pygments") has no background color, so
# fenced code blocks were rendering with no visible box at all in the PDF
# (the website gets one for free from Material's own CSS for `pre`/`code`).
# This is "monochrome" (no per-token syntax colouring -- deferred, see
# README's Known limitations) with a background matching the shading the
# source .docx used for its "Code" paragraph style.
CODE_HIGHLIGHT_STYLE = Path(__file__).parent / "pdf-code-block.theme"
# hyperref's PDF bookmarks omit section numbers by default
# (bookmarksnumbered=false) even with --number-sections numbering the
# headings themselves. \hypersetup can't go in the preamble via
# header-includes here -- pandoc's template loads the bookmark/hyperref
# package *after* header-includes runs, so \hypersetup is still
# undefined at that point (confirmed: pdflatex errors "Undefined control
# sequence" at exactly that line). --include-before-body inserts after
# \begin{document}, by which point hyperref is fully loaded, and
# \hypersetup works equally well called from there.
PDF_BOOKMARKS_TEX = Path(__file__).parent / "pdf-bookmarks.tex"
# Level-4 headings (#### in docs/*.md) become LaTeX's \paragraph, whose
# default in the article class (this document's base class) is a
# "run-in" heading: bold text immediately followed by its first
# paragraph's text on the same line, no line break -- unlike every
# numbered level above it (\subsubsection and up), which all start their
# own line. Confirmed against the source .docx's own PDF export, which
# puts its "Heading 4"-styled headings on their own line same as every
# other heading level. \renewcommand{\paragraph}'s \@startsection call is
# identical to article.cls's own definition except the after-heading
# skip is a small positive length instead of a small negative one --
# by convention that's what actually switches \@startsection from
# "run-in" to "own line, own paragraph" mode, not a distinct setting.
PDF_HEADING_FIX_TEX = Path(__file__).parent / "pdf-heading-fix.tex"
# The source .docx's "Normal"/heading styles both set rFonts to Montserrat
# (confirmed in word/styles.xml -- Heading1Char explicitly, everything else
# via Normal's docDefaults override), a sans-serif with a noticeably larger
# x-height and looser default spacing than Latin Modern Roman, the serif
# face xelatex falls back to when no mainfont is set at all (Pandoc's
# default template only loads `lmodern`, never sets one). That mismatch is
# most of why the PDF reads as a different, tighter document than the
# original despite sharing the same margins/paragraph spacing. Montserrat
# isn't installed on this machine (or reliably on anyone else's -- it's a
# Google Font, not a Windows/Office one), so static weights are vendored
# under scripts/fonts/Montserrat/ rather than relying on system font
# discovery: instantiated once from Google's variable-font source
# (google/fonts no longer publishes static Montserrat files) via
# `fonttools varLib.instancer`, wght=400/700 for Regular/Bold, upright and
# italic. Referencing them by explicit file (mainfontoptions' Upright/Bold/
# Italic/BoldItalicFont keys + Path) works with plain xelatex regardless of
# what's registered in the OS font database.
MONTSERRAT_DIR = Path(__file__).parent / "fonts" / "Montserrat"
# See force_figure_placement below for what this fixes (a figure's image
# and caption silently overflowing/combining across a page boundary) and
# pdf-figure-fix.tex itself for the two-part mechanism.
PDF_FIGURE_FIX_TEX = Path(__file__).parent / "pdf-figure-fix.tex"

# The source .docx's footer (word/footer2.xml et al.) carries a copyright
# line, the document's Version/Date, and a page number -- none of which
# Pandoc's LaTeX writer or article.cls's default \pagestyle{plain} produce
# (the latter is a bare centered page number, nothing else). docs/ is the
# master now (see CLAUDE.md's Architecture section), so these fields are
# read directly out of docs/*.md rather than the retired .docx:
# Version/Date already exist as plain text on the cover page (see
# cover_page_latex, COVER_VERSION_RE/COVER_DATE_RE), and the copyright
# year range is read the same way from the Copyright section's own text
# in docs/front-matter/preface.md, so a future revision bump or copyright
# year change only ever needs a docs/*.md edit, never a build_pdf.py edit.
FOOTER_COPYRIGHT_FILE = "front-matter/preface.md"
FOOTER_COPYRIGHT_RE = re.compile(r"Copyright ©\s*[0-9]{4}-([0-9]{4})\s+WITS Protocol Standards Association")
# Generated fresh each build (unlike the other PDF_*_TEX fixes above,
# which are static files checked into the repo) since its content is
# data, not a fixed patch -- see footer_fix_tex.
PDF_FOOTER_FIX_TEX = BUILD / "pdf-footer-fix.tex"


def read_footer_fields(docs: Path) -> tuple[str, str, str]:
    cover = (docs / COVER_FILE).read_text(encoding="utf-8")
    version = COVER_VERSION_RE.search(cover).group(1)
    date = COVER_DATE_RE.search(cover).group(1)
    # The footer's own copyright line uses just the latest year (matching
    # word/footer2.xml's "© 2026 WITS Protocol..." in the source .docx),
    # not the full start-end range the Copyright section's formal legal
    # notice uses just above in the same file -- a deliberately shorter
    # form for a running footer than the front matter's own notice.
    preface = (docs / FOOTER_COPYRIGHT_FILE).read_text(encoding="utf-8")
    latest_year = FOOTER_COPYRIGHT_RE.search(preface).group(1)
    return version, date, latest_year


def read_spec_version() -> dict | None:
    """spec-version.json is written by fetch_spec.py at fetch time, not
    checked into this repo -- absent if fetch_spec.py hasn't been run yet,
    which read_footer_fields/cover_page_latex already require regardless
    (they read straight out of DOCS), so this only ever adds information,
    never blocks the build on its own."""
    if not SPEC_VERSION_FILE.exists():
        return None
    return json.loads(SPEC_VERSION_FILE.read_text(encoding="utf-8"))


# Builds the whole footer -- portrait and landscape alike -- from eso-pic's
# absolute page-position hooks (\AddToShipoutPictureBG/FG) rather than
# fancyhdr's \fancyfoot, which is how an earlier version of this function
# did the portrait footer. That turned out not to be a viable mechanism at
# all for multi-line content here, not just an awkward one: confirmed by
# hand, in a from-scratch bisection, that fancyhdr silently drops whichever
# paragraph/row/line ends up *last* in a \fancyfoot argument once that
# argument has more than one line of content and is spread across many
# pages -- true regardless of whether the lines came from a tabular row, a
# minipage paragraph, or a plain \\-separated line, and regardless of the
# content's own text. It's fancyhdr's own re-expansion of that stored
# argument once per page (via TeX's \output routine, a restricted context --
# every affected build logged "Overfull ... while \output is active") that's
# fragile, not any one thing built on top of it. \AddToShipoutPictureBG/FG
# hook into atbegshi's per-page shipout instead, which every test here
# reproduced the bug-free 100% of the time -- and it's already what the
# landscape footer below uses, unchanged, for the same reason (see the
# landscape-specific comment further down).
#
# Absolute placement also sidesteps the fragile-if-it-has-to-wrap \hfill
# trick a \fancyfoot-based layout would still have needed for "centered
# copyright, right-justified version on the same line": each piece here
# gets its own \put at a precomputed x, with \makebox's own [l]/[r]/(default
# centered) alignment -- no glue, no line-breaking involved at all.
FOOTER_MARGIN_MM = 15  # matches -V geometry:margin=15mm below
FOOTER_LOGO_WIDTH_MM = 20
# lucid-wordmark.png (extracted from the source .docx's word/media/image2.png
# -- the actual footer image word/footer2.xml embeds, confirmed against its
# own _rels; distinct from docs/assets/images/lucid-logo.png, which despite
# its name is the WITS wordmark used on the cover page) is 370x145px, a
# 2.55:1 aspect ratio -- sized here to a fixed width since the position
# math below needs one either way, letting height follow from that.
FOOTER_GAP_MM = 3


def footer_fix_tex(docs: Path, version: str, date: str, latest_year: str, spec_info: dict | None) -> str:
    logo = str((docs / "assets" / "images" / "lucid-wordmark.png").resolve()).replace("\\", "/")
    rest_left = f"\\dimexpr{FOOTER_MARGIN_MM}mm+{FOOTER_LOGO_WIDTH_MM}mm+{FOOTER_GAP_MM}mm\\relax"
    right_edge = f"\\dimexpr\\paperwidth-{FOOTER_MARGIN_MM}mm\\relax"
    rule_width = f"\\dimexpr\\paperwidth-{2 * FOOTER_MARGIN_MM}mm\\relax"
    # spec_info is None only if fetch_spec.py hasn't been run (see
    # read_spec_version) -- the version line still reads fine without it,
    # just without the trailing provenance clause.
    spec_suffix = ""
    if spec_info is not None:
        spec_label = spec_info["tag"] or spec_info["commit_short"]
        spec_suffix = f" | spec {spec_label}"
    return "\n".join([
        r"\usepackage{eso-pic}",
        rf"\newcommand{{\lucidfootercopyright}}{{Copyright \textcopyright\ {latest_year} WITS Protocol Standards Association. All rights reserved.}}",
        rf"\newcommand{{\lucidfooterversion}}{{Version: {version} | {date}{spec_suffix}}}",
        r"\pagestyle{empty}",
        # \iflucidlandscapefooter switches the whole page from this portrait
        # footer to the rotated one further down (see LANDSCAPE_PAGES in
        # main() for where \lucidenable/\luciddisablelandscapefooter get
        # called); \iflucidsuppressfooter blanks it entirely for the cover
        # page (see cover_page_latex) -- titlepage's own \thispagestyle{
        # empty} has no effect on eso-pic's hooks, which fire for every
        # page regardless of \pagestyle, so the cover needs its own switch.
        r"\newif\iflucidlandscapefooter",
        r"\newif\iflucidsuppressfooter",
        r"\newcommand{\lucidfooterportrait}{%",
        r"  \begin{picture}(0,0)",
        rf"  \put(\LenToUnit{{{FOOTER_MARGIN_MM}mm}},\LenToUnit{{16mm}}){{\rule{{\LenToUnit{{{rule_width}}}}}{{0.4pt}}}}",
        rf"  \put(\LenToUnit{{{FOOTER_MARGIN_MM}mm}},\LenToUnit{{3.5mm}}){{\makebox(0,0)[bl]{{\includegraphics[width={FOOTER_LOGO_WIDTH_MM}mm]{{{logo}}}}}}}",
        # Copyright and page number share the upper row; version/date gets
        # its own row below, right-aligned under the page number -- two
        # independent \put's each (not one shared text line, e.g. via
        # \hfill) since a wide copyright line plus a same-line
        # right-justified neighbour collided at this column width
        # (confirmed by hand: "reserved." and "Version:" overlapped once).
        # The whole block sits a few mm clear of the physical page edge
        # (3.5mm at the logo/version's own baseline) rather than flush
        # against it.
        rf"  \put(\LenToUnit{{{rest_left}}},\LenToUnit{{9mm}}){{\makebox(0,0)[l]{{\footnotesize\lucidfootercopyright}}}}",
        rf"  \put(\LenToUnit{{{right_edge}}},\LenToUnit{{9mm}}){{\makebox(0,0)[r]{{\footnotesize\thepage}}}}",
        rf"  \put(\LenToUnit{{{right_edge}}},\LenToUnit{{5mm}}){{\makebox(0,0)[r]{{\footnotesize\lucidfooterversion}}}}",
        r"  \end{picture}%",
        r"}",
        # The landscape page's raw, pre-rotation MediaBox is still plain A4
        # portrait, identical to every other page -- pdflscape sets the PDF
        # page's /Rotate attribute (90) and relies on the *viewer* to rotate
        # the display, rather than ever laying out a physically wide page.
        # Anything drawn at the bottom-center of that unrotated frame -- like
        # \lucidfooterportrait above -- maps to the *left* edge of what's
        # displayed once a PDF viewer applies a 90-degree clockwise
        # rotation, not the bottom (confirmed by hand: the page number
        # rendered correctly, just on the wrong edge, before this existed).
        # Reaching the frame's *right* margin instead (which that rotation
        # maps to the display's bottom edge) needs absolute placement here
        # too; \rotatebox{90} on the content is what then cancels out the
        # viewer's own rotation so the text reads upright.
        r"\newcommand{\lucidfooterlandscape}{%",
        r"  \put(\LenToUnit{\paperwidth-10mm},\LenToUnit{0.5\paperheight}){%",
        r"    \rotatebox{90}{\makebox(0,0)[b]{%",
        r"      \footnotesize\thepage\quad\lucidfootercopyright\quad\lucidfooterversion%",
        r"    }}%",
        r"  }%",
        r"}",
        r"\AddToShipoutPictureBG{\AtPageLowerLeft{\iflucidsuppressfooter\else\iflucidlandscapefooter\else\lucidfooterportrait\fi\fi}}",
        r"\AddToShipoutPictureFG{\AtPageLowerLeft{\iflucidlandscapefooter\lucidfooterlandscape\fi}}",
        r"\newcommand{\lucidenablelandscapefooter}{\lucidlandscapefootertrue}",
        r"\newcommand{\luciddisablelandscapefooter}{\lucidlandscapefooterfalse}",
        r"\newcommand{\lucidsuppressfooter}{\lucidsuppressfootertrue}",
        r"\newcommand{\lucidrestorefooter}{\lucidsuppressfooterfalse}",
    ])


CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
# Linux installs put the browser on PATH under one of these names rather
# than at a fixed path (GitHub's ubuntu runners ship google-chrome).
CHROME_PATH_NAMES = [
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge",
]

# pandoc's LaTeX PDF path needs rsvg-convert to embed SVGs directly, which
# isn't installed here (and is a painful Windows dependency to add). A
# headless Chromium browser is a perfectly good SVG rasterizer and is
# already on the machine, so use it to pre-render each SVG to PNG for the
# PDF build only -- docs/ keeps the SVG originals (smaller, text-diffable,
# and MkDocs/GitHub render them natively).
SVG_ROOT_DIMS = re.compile(r'<svg\b[^>]*?\bwidth="([\d.]+)"[^>]*?\bheight="([\d.]+)"')


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    for name in CHROME_PATH_NAMES:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "No headless-capable Chrome/Chromium/Edge found to rasterize SVGs "
        f"(looked for {', '.join(CHROME_CANDIDATES + CHROME_PATH_NAMES)})"
    )


def rasterize_svgs() -> None:
    RASTER_DIR.mkdir(parents=True, exist_ok=True)
    browser = find_browser()
    for svg in sorted((DOCS / "assets" / "images").glob("*.svg")):
        m = SVG_ROOT_DIMS.search(svg.read_text(encoding="utf-8"))
        width, height = (int(float(v)) for v in m.groups()) if m else (1000, 750)
        out_png = RASTER_DIR / (svg.stem + ".png")
        subprocess.run(
            [
                browser, "--headless=new", "--disable-gpu",
                f"--screenshot={out_png}",
                f"--window-size={width},{height}",
                "--default-background-color=FFFFFFFF",
                # Chrome's headless screenshot is a viewport capture, not
                # a content-bounds crop: requesting a window-size exactly
                # matching the SVG's own declared width/height leaves no
                # slack for Chrome's own scrollbar UI, which then shows up
                # baked into the PNG (and from there, into the PDF)
                # whenever the page's scrollable area is even a pixel
                # taller/wider than the viewport -- confirmed by hand,
                # most visible on the largest diagrams. This flag
                # suppresses the scrollbar chrome entirely rather than
                # trying to pad the window size enough to avoid it.
                "--hide-scrollbars",
                svg.resolve().as_uri(),
            ],
            check=True, capture_output=True,
        )


def nav_files(nav) -> list[str]:
    """Flatten mkdocs.yml's (possibly nested) nav list into an ordered list
    of file paths relative to docs_dir."""
    files = []
    for entry in nav:
        # each entry is a single-key dict {title: str-or-nested-list}, e.g.
        # {"Home": "index.md"} or {"Data model": [{...}, {...}]}
        (value,) = entry.values()
        if isinstance(value, str):
            files.append(value)
        else:
            files.extend(nav_files(value))
    return files


# collapse a possibly-relative image/link path down to one relative to docs/
# root: "../../assets/images/x.png" -> "assets/images/x.png",
# "../front-matter/references.md#id" -> "front-matter/references.md#id"
LEADING_DOTDOT = re.compile(r"^(?:\.\./)+")


def normalize_paths(content: str) -> str:
    def fix_image(m):
        alt, path = m.group(1), m.group(2)
        path = LEADING_DOTDOT.sub("", path)
        if path.endswith(".svg"):
            path = path[: -len(".svg")] + ".png"  # see rasterize_svgs()
        return f"![{alt}]({path})"

    content = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", fix_image, content)

    # cross-file anchor links (`[text](path/to/file.md#id)`) become plain
    # intra-document anchors once every file is concatenated into one PDF
    # source -- the id still exists somewhere in the merged document.
    content = re.sub(r"\]\([^)#]*\.md(#[A-Za-z][\w-]*)\)", r"](\1)", content)

    # pdflatex/xelatex's default fonts don't carry emoji glyphs (e.g. the
    # NOTICE callouts' info-source icon) -- swap in a plain-text label for
    # the PDF only; the website renders the emoji natively.
    content = content.replace("\U0001F6C8", "**NOTE:**")

    # Preface headings (Disclaimer, Copyright, Notices, Glossary, ...) carry
    # a .Heading-preface class from the source .docx's own "Heading-preface"
    # style, but they're not part of the numbered chapter sequence in the
    # original document -- --number-sections doesn't know that on its own,
    # and would otherwise count them as chapters 1-7, pushing "Introduction"
    # to chapter 8 and every section number after it out of step with the
    # original. Mark them unnumbered so numbering starts fresh at
    # "1 Introduction", matching the source.
    content = re.sub(r"(?m)^(#{1,6} .*\{[^}]*\.Heading-preface[^}]*)\}$", r"\1 .unnumbered}", content)
    return content


# The source .docx's "Heading1" style (every numbered top-level section --
# Introduction, Data model, ... -- plus, since "Appendix H1" is based on
# it and never overrides the property, every appendix) sets
# pageBreakBefore unconditionally; every level below it (Heading2 and
# down) explicitly resets pageBreakBefore back to false, and so does
# "Heading preface" (Disclaimer, Copyright, Notices, Conventions,
# Glossary, References -- despite also being nominally H1-level,
# confirmed in word/styles.xml). Pandoc's LaTeX writer has no notion of
# "page break before a heading" at all -- `\section` never forces one on
# its own -- so left alone every H1 flows straight on from whatever
# came before it, unlike the source. `docs/*.md` H1s (`# `, always at
# column 0 -- nothing here nests inside a blockquote/list, so there's no
# risk of matching a body '#' at a deeper indent) split cleanly into the
# same two groups the .docx does: plain `# Heading` (numbered chapters)
# and `# Heading {#id .Appendix-H1}` both want the break; `# Heading {#id
# .Heading-preface}` doesn't. Matching on the *absence* of
# `.Heading-preface` (rather than an explicit allow-list of "regular or
# .Appendix-H1") means a plain heading and an .Appendix-H1 one are
# handled identically without the two needing to be spelled out
# separately here.
H1_HEADING_RE = re.compile(r"(?m)^# .*$")


def add_section_page_breaks(content: str) -> str:
    def inject(m: re.Match) -> str:
        heading = m.group(0)
        if ".Heading-preface" in heading:
            return heading
        return "```{=latex}\n\\clearpage\n```\n\n" + heading

    return H1_HEADING_RE.sub(inject, content)


# The "NOTE:"/info callout boxes throughout docs/*.md (originally the
# source .docx's info-icon callouts, see normalize_paths) are written as
# a pipe table with *only* a header row and no body rows at all -- valid
# GFM, and the only content that table has, but it means Pandoc's own
# reader treats every such callout as a table with an empty body. A `+`
# here (one or more body rows) made every one of these invisible to
# set_pipe_table_widths -- its regex simply didn't match them, so they
# fell through untouched, still carrying whatever dash-count ratio was
# typed (or came out of convert.py's docx conversion) for their
# separator row, with no floor-width protection at all. That silently
# reproduced the overlap bug for one of them (03-controls-and-
# overrides.md's "NOTE:" callout, a 5:68 dash ratio -- far narrower than
# bold "NOTE:" needs, confirmed by hand: "NOTE" ran into its own colon
# and across the grid line into the next column) despite ten other
# identically-styled callouts elsewhere in the document, at more
# forgiving ratios, rendering correctly by luck rather than by any actual
# protection. `*` (zero or more) instead matches these too, since an
# empty body is exactly what a real header-only table has -- the width
# computation below already treats header_cells as part of the same
# per-column totals/floors as body cells, so a table with nothing *but*
# a header row is just the degenerate case of the same math, not a
# separate one.
PIPE_TABLE_RE = re.compile(
    r"(?m)^(?P<header>\|.*\|)\n"
    r"(?P<sep>\|(?:[ \t]*:?-+:?[ \t]*\|)+)\n"
    r"(?P<body>(?:\|.*\|\n)*)"
)
MIN_COLUMN_FRACTION = 0.08
SEPARATOR_DASH_BUDGET = 100
# Approximate physical geometry of a PDF table, matching this script's own
# A4 + 15mm-margin page: enough to convert a column's longest single
# unbreakable token (in characters) into a safe minimum width fraction.
# Deliberately generous (real Latin Modern Roman 10pt averages under
# 5.5pt/char) since undershooting reproduces the overlap bug, while
# overshooting only costs a little extra width on one column.
CONTENT_WIDTH_PT = 512
TABCOLSEP_PT = 6
CHAR_WIDTH_PT = 6.0


def _row_cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip()[1:-1].split("|")]


def _strip_markup(cell: str) -> str:
    return re.sub(r"<[^>]+>|\*\*|\*|`", "", cell)


def _visible_length(cell: str) -> int:
    return len(_strip_markup(cell))


def _longest_token(cell: str) -> int:
    tokens = _strip_markup(cell).split()
    return max((len(t) for t in tokens), default=0)


def set_pipe_table_widths(content: str) -> str:
    """GitHub and MkDocs render pipe tables as plain HTML and let the
    browser auto-size columns by content -- a separator row's dash count
    is cosmetic there (the GFM spec gives it no meaning beyond "at least
    one"), so docs/*.md authors never need to think about it. Pandoc's
    `markdown` reader (used here -- its `gfm` reader doesn't support the
    `: Caption` syntax our table titles rely on) is the exception: it
    turns relative dash-run length into each column's LaTeX width
    fraction, and with a uniform dash count every column ends up equally
    wide regardless of content, so free-text columns wrap onto many lines
    while short numeric/enum columns sit mostly empty. Recompute each
    table's dash lengths proportional to its actual cell content at PDF
    build time -- run here rather than baked into docs/*.md once by
    convert.py, so it applies equally to every table regardless of
    whether it came from the original .docx conversion or was written by
    hand afterwards.

    Each column also gets a minimum width sized to its single longest
    unbreakable token (a header word or a camelCase JSON name, since
    neither has a space for LaTeX to wrap at). Relying on LaTeX to
    hyphenate an overly narrow column instead doesn't hold up: a header
    cell's word not fitting its column silently overflows into the next
    column with no warning and no wrap -- confirmed by hand, and not
    conditional on hyphenation patterns being available (this holds even
    with hyphenation fully working, since it can't help programmatic
    identifiers like `reportPeriod` anyway)."""

    def widths_for_table(m: re.Match) -> str:
        header_cells = _row_cells(m.group("header"))
        ncols = len(header_cells)
        totals = [_visible_length(c) for c in header_cells]
        max_tokens = [_longest_token(c) for c in header_cells]
        for row in m.group("body").splitlines():
            for i, cell in enumerate(_row_cells(row)):
                if i < ncols:
                    totals[i] += _visible_length(cell)
                    max_tokens[i] = max(max_tokens[i], _longest_token(cell))

        total = sum(totals) or 1
        fractions = [t / total for t in totals]

        content_width = CONTENT_WIDTH_PT - 2 * (ncols - 1) * TABCOLSEP_PT
        # The first and last column each give up two \tabcolsep of their
        # own text-wrapping width in the final LaTeX: one from the
        # column's own width formula shrinking (_shrink_column_width) and
        # a second from the \leftskip/\rightskip _pad_body_row_cells/
        # _process_header_cell set inside the cell's own text to keep
        # that inset on every wrapped line, not just the first (see
        # add_table_grid_lines's module comment) -- a single column pays
        # both insets twice over if there's only one. A column's actual
        # usable width is `frac * content_width - inset_pt` (the inset is
        # a flat point deduction, not a proportional one), so the floor
        # has to add the inset back on the *token* side of the division
        # -- `tok * CHAR_WIDTH_PT + inset_pt` over the plain
        # content_width -- rather than subtract it from content_width
        # first: subtracting from the denominator understates the floor
        # (divides the same token requirement by a smaller number, but
        # not by enough to offset a flat pt deduction happening after the
        # multiply, not before it), which silently reproduced the very
        # overflow floors exist to prevent for Table 11's "Information"/
        # "Persistence" header, confirmed by hand once the more forgiving
        # divisive version had already shipped.
        inset_cols = {0, ncols - 1} if ncols > 1 else {0}
        floors = [
            max(
                MIN_COLUMN_FRACTION,
                min(0.6, (tok * CHAR_WIDTH_PT + (4 if ncols == 1 else 2) * TABCOLSEP_PT) / content_width)
                if i in inset_cols
                else min(0.6, tok * CHAR_WIDTH_PT / content_width),
            )
            for i, tok in enumerate(max_tokens)
        ]

        # Every column gets at least its floor; anything beyond that is
        # only taken from columns that have room above their own floor,
        # scaled down just enough to fit -- never below the floor itself,
        # which a plain proportional subtraction can do once enough
        # columns are floor-bound (verified against the 11-column
        # Operation Codes table: two flexible columns absorbing the
        # deficit from nine floor-bound ones went negative and silently
        # clamped to the 3-dash absolute minimum -- the opposite of what
        # a floor is supposed to guarantee).
        ideal = [max(f, floor) for f, floor in zip(fractions, floors)]
        total_ideal = sum(ideal)
        if total_ideal > 1.0:
            headroom = [v - floor for v, floor in zip(ideal, floors)]
            headroom_total = sum(headroom)
            excess = total_ideal - 1.0
            if headroom_total >= excess and headroom_total > 0:
                ideal = [v - excess * (h / headroom_total) for v, h in zip(ideal, headroom)]
            else:
                # Even every column at its own floor doesn't fit -- fall
                # back to a uniform scale-down (relative proportions kept,
                # floors no longer guaranteed, but still never negative).
                ideal = [v / total_ideal for v in ideal]
        elif total_ideal < 1.0:
            ideal = [v / total_ideal for v in ideal]
        fractions = ideal

        # ceil, not round: Pandoc only ever sees this dash count, not the
        # exact float fraction above it -- rounding a floor-bound column's
        # fraction *down* to the nearest dash (out of a SEPARATOR_DASH_BUDGET
        # of 100, so each dash is worth a full 1%) can and did shave it just
        # under its own computed floor, reproducing the very overlap the
        # floor exists to prevent: confirmed by hand for two of the "NOTE:"/
        # "DISCLAIMER" info-callout columns (see the module comment on
        # PIPE_TABLE_RE), whose exact floors were 0.084/0.144 but which
        # rendered at a plain-rounded 0.08/0.14 -- both silently below floor.
        # Rounding every column up instead can only ever give a column *at
        # least* its intended share (at most one dash-unit, 1%, more), never
        # less, and costs nothing observable on any of this document's other
        # tables since it's the floor-bound columns -- previously the ones
        # with zero slack to lose -- that actually benefit.
        dash_counts = [max(3, math.ceil(f * SEPARATOR_DASH_BUDGET)) for f in fractions]
        sep = "|" + "|".join("-" * n for n in dash_counts) + "|\n"
        return m.group("header") + "\n" + sep + m.group("body")

    return PIPE_TABLE_RE.sub(widths_for_table, content)


# A pipe-table row has to be exactly one physical line, so a table cell
# whose source content (e.g. a docx list flattened into "•"-prefixed
# clauses by convert.py -- see its own module docstring) needs multiple
# visual lines has nowhere to put an actual newline. `<br>` is the
# GFM-native way to work around that: plain raw HTML, rendered correctly
# as-is by GitHub's own file preview and by MkDocs (Python-Markdown's
# `tables` extension passes inline HTML straight through), so docs/*.md
# itself never needs anything PDF-specific. Pandoc's `markdown` reader
# disagrees, though -- confirmed by hand that unlike its `html` reader
# (where `<br>` is a recognized hard-break token), raw inline HTML
# reaching the `markdown` reader this way survives into the AST as inert
# raw content that the LaTeX writer then silently drops, not just
# un-rendered but gone, taking with it the only separation between the
# words on either side of it.
#
# `` `\newline{}`{=latex} `` (backtick-delimited raw LaTeX via the
# `raw_attribute` extension, on by default in `markdown`) is the
# replacement -- NOT `` `\\`{=latex} ``, which was tried first and looked
# right (a literal `\\` came through into the generated .tex exactly as
# asked) but silently produced a garbled, misaligned table: confirmed by
# hand that `\arraybackslash` -- already in every column's own type
# declaration, see _add_grid_colspec -- exists to *restore* `\\`'s normal
# table meaning (end this row) inside a p{} column, undoing the
# `\newline` meaning `\raggedright` would otherwise have locally given it
# there; it does not, as its name misleadingly suggests, make `\\` safe
# to use as a manual line break. `\newline` is a distinct command with no
# such row-ending meaning, so it doesn't hit this at all -- confirmed by
# hand against a standalone reproduction of this exact column type before
# touching the real document. The trailing `{}` keeps `\newline`'s own
# control-word name from swallowing whatever letter starts the next
# clause (`\newlineTicket` is a different, undefined control sequence to
# TeX) since pandoc's raw-LaTeX span inserts the text with nothing after
# it to force the word to end.
#
# Runs after set_pipe_table_widths, not before: `<br>` is one of the tag
# forms _strip_markup already knows to discard when measuring a cell's
# width, but the raw-LaTeX replacement text is plain backtick/backslash/
# brace text with no such exemption, and would otherwise inflate that
# column's measured width for no reason.
TABLE_LINE_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
LATEX_LINE_BREAK_TEX = r"`\newline{}`{=latex}"


def convert_table_line_breaks(content: str) -> str:
    def convert_table(m: re.Match) -> str:
        # re.sub's *replacement string* form treats backslashes as escapes/
        # backreferences (e.g. "\\" collapses to one literal backslash,
        # silently halving LATEX_LINE_BREAK_TEX's pair down to one) --
        # a replacement function's return value is inserted verbatim, with
        # no such reinterpretation, so it's the only safe way to substitute
        # in a replacement that itself contains backslashes.
        header = TABLE_LINE_BREAK_RE.sub(lambda _: LATEX_LINE_BREAK_TEX, m.group("header"))
        body = TABLE_LINE_BREAK_RE.sub(lambda _: LATEX_LINE_BREAK_TEX, m.group("body"))
        return header + "\n" + m.group("sep") + "\n" + body

    return PIPE_TABLE_RE.sub(convert_table, content)


# The source .docx draws a full grid -- lines around every cell -- for
# every table, but Pandoc's LaTeX table writer has no option for that: it
# hardcodes the booktabs style (rules only at the very top/bottom and
# between header/body, never vertical, never between body rows), for
# every table pandoc emits, with no command-line flag or template
# variable to change it. Reproducing the source's grid style means
# patching Pandoc's own LaTeX output text after the fact, which is the
# whole reason this script stops at an intermediate .tex file (see
# COMBINED_TEX) instead of letting Pandoc drive xelatex directly the way
# it normally would from a single `-o *.pdf` call.
#
# This only has to handle exactly the shape Pandoc's LaTeX writer emits
# for every pipe table in this document (confirmed by hand against the
# generated .tex, and true for all 44 of them: every column here is
# `>{\raggedright\arraybackslash}p{...}` -- left-aligned is the only
# alignment any table in docs/ uses) -- not LaTeX table syntax in
# general:
#
#   \begin{longtable}[]{@{}
#     >{\raggedright\arraybackslash}p{(\linewidth - 2\tabcolsep) * \real{0.2000}}
#     >{\raggedright\arraybackslash}p{(\linewidth - 2\tabcolsep) * \real{0.8000}}@{}}
#   \caption{...}\tabularnewline          <- only if the table has a caption
#   \toprule\noalign{}
#   <header row(s), possibly repeated for \endfirsthead>
#   \midrule\noalign{}
#   \endhead
#   \bottomrule\noalign{}
#   \endlastfoot
#   <body rows, one per physical line -- relies on --wrap=none below>
#   \end{longtable}
LONGTABLE_COLSPEC_RE = re.compile(r"\\begin\{longtable\}\[\]\{@\{\}\n(.*?)@\{\}\}\n", re.S)
COLUMN_TOKEN_RE = re.compile(
    r">\{\\raggedright\\arraybackslash\}p\{\(\\linewidth - \d+\\tabcolsep\) \* \\real\{[\d.]+\}\}"
)
ROW_BLOCK_RE = re.compile(r"\\endlastfoot\n(.*?)\\end\{longtable\}", re.S)




# Every column's own text needs \tabcolsep of padding between it and
# whichever edge(s) of the table -- leading, trailing, or (for a 1-column
# table) both -- sit against the outer grid rule this function adds
# (without it, e.g. Table 11's first column looked hard-left, unlike
# every interior column, whose "|" always sits between two ordinary,
# already-padded column gaps). That padding has to come from a
# \leftskip/\rightskip set as the first/last thing *inside* each cell's
# own content -- for a header cell, inside the column's \begin{minipage}[
# t]{\linewidth}\raggedright wrapper; for a body cell, directly in its own
# plain text -- not from the column *type*'s own >{...}/<{...} hooks
# (array.sty), even though those look like the obvious place for a
# per-column change applying to every row alike. Two earlier approaches
# confirmed by hand why not: shifting the outer rule itself sideways via
# the colspec's leading/trailing "@{...}" left \hline (added after every
# row below) too short to reach it, visibly gapping every row boundary;
# and putting the skip in the column type's own >{...} hook -- which runs
# identically for every row, header included -- silently broke only the
# header row's vertical alignment: a lone minipage sitting right after
# other (non-empty) material in the same table cell doesn't reliably
# honor its own [t]/[b] alignment in this longtable setup, for reasons
# never fully pinned down in TeX's box model, while a column with nothing
# before its minipage renders correctly. See _process_header_cell and
# _pad_body_row_cells for the per-cell version that avoids both failure
# modes.
#
# \leftskip/\rightskip (rather than an \hspace box) is what makes the
# padding survive a wrap: \hspace is inline content, spent entirely on
# whichever single line it sits on (the first, for a leading inset), so a
# cell whose text wraps to a second line had that later line flush
# against the grid rule instead of matching the first line's indent --
# confirmed by hand against Table 14's "Parent Point Type"/"Point Limit
# Index" wrapping. \leftskip/\rightskip are paragraph-level glue read
# once at the cell's own implicit \par (its closing \\ or
# \end{minipage}), so whichever value is current at that point applies
# to *every* line the line-breaker produces for that paragraph alike --
# assigning them anywhere earlier in the same cell, first line or last,
# reproduces the fixed \tabcolsep inset on every wrapped line evenly.
#
# The column's own *width formula* still has to shrink by \tabcolsep per
# inset edge here, even though the padding itself no longer comes from
# this column-type declaration -- confirmed by hand, oddly, that this is
# load-bearing for the header fix above, not just cosmetic: with the
# width formula left at its full, un-shrunk value, the header minipage's
# [t] alignment reverted to behaving like [b] again (text pinned to the
# *bottom* of a row another column forces taller) even though nothing
# about the padding mechanism itself had changed, and shrinking the width
# formula back down made it behave correctly again. No explanation for
# *why* was found; set_pipe_table_widths's own floor-compensation comment
# already accounts for this same \tabcolsep regardless of the reason.
# Unlike the old \hspace, though, \leftskip/\rightskip land inside the
# cell's own wrapping width on *every* line, not just one -- so the text
# actually available to wrap in is the already-shrunk box width *minus*
# the skip too, one full extra \tabcolsep per inset edge beyond what the
# box-width shrink alone accounts for; set_pipe_table_widths's floor
# computation doubles its per-edge \tabcolsep deduction to match.
COLUMN_TOKEN_DETAIL_RE = re.compile(
    r"^>\{\\raggedright\\arraybackslash\}p\{\(\\linewidth - (\d+)\\tabcolsep\) \* \\real\{([\d.]+)\}\}$"
)


def _shrink_column_width(token: str, *, insets: int) -> str:
    n, frac = COLUMN_TOKEN_DETAIL_RE.match(token).groups()
    return rf">{{\raggedright\arraybackslash}}p{{(\linewidth - {n}\tabcolsep) * \real{{{frac}}} - {insets}\tabcolsep}}"


def _add_grid_colspec(m: re.Match) -> str:
    cols = COLUMN_TOKEN_RE.findall(m.group(1))
    if len(cols) == 1:
        cols[0] = _shrink_column_width(cols[0], insets=2)
    else:
        cols[0] = _shrink_column_width(cols[0], insets=1)
        cols[-1] = _shrink_column_width(cols[-1], insets=1)
    joined = "|\n".join("  " + c for c in cols)
    return "\\begin{longtable}[]{|@{}\n" + joined + "@{}|}\n"


# \relax terminates each assignment so it can't go on consuming further
# tokens (dimen/glue assignments otherwise scan ahead looking for more
# unit text) before the cell's actual text begins. \rightskip keeps
# \raggedright's own "plus 1fil" stretch component -- only its fixed part
# changes from 0pt to \tabcolsep -- so ragged lines still end whenever a
# word naturally does, just never closer than \tabcolsep to the rule.
LEFT_INSET_TEX = r"\leftskip=\tabcolsep\relax "
RIGHT_INSET_TEX = r"\rightskip=\tabcolsep plus 1fil\relax "

# Vertical counterpart to LEFT_INSET_TEX/RIGHT_INSET_TEX, applied to
# *every* cell (not just the first/last column) since every row's text
# touched the \hline above/below it, not just the outer edges. A row's
# height/depth in a plain array/tabular is just whatever its tallest
# cell's own content needs -- there's no padding at all by default,
# which is why a single-line cell's text sits flush against both rules.
# \arraystretch (see pdf-table-padding.tex, an earlier attempt at this
# fix) only raises the *minimum* row height/depth via an invisible strut
# inserted once per row -- confirmed by hand that this does nothing for
# a row whose tallest cell already exceeds that minimum, which is any
# multi-line cell: its own last line still touched the rule below
# regardless of \arraystretch (Table 12's wrapped "JSON Name" header,
# Table 11's 4-line "Persistence" body cell). A zero-width \rule with an
# explicit height (no depth) inserted as the literal first token of a
# cell's paragraph, or one with an explicit depth (no height) as the
# literal last token, instead adds height/depth to *just* that cell's
# own first/last line -- unlike \leftskip/\rightskip, this deliberately
# is not a paragraph-level parameter, since a strut on every line would
# space out a wrapped cell's interior lines too, not just pad its top and
# bottom -- so it stacks correctly regardless of how many lines (or how
# tall any neighboring cell) end up in between.
TOP_STRUT_TEX = r"\rule{0pt}{4pt}"
BOTTOM_STRUT_TEX = r"\rule[-4pt]{0pt}{0pt}"


def _pad_body_row_cells(line: str) -> str:
    stripped = line.rstrip()
    trailing_ws = line[len(stripped):]
    content = stripped[:-2].rstrip()  # strip the trailing "\\" row-end command
    cells = content.split(" & ")
    cells[0] = LEFT_INSET_TEX + cells[0]
    cells[-1] = cells[-1] + RIGHT_INSET_TEX
    cells = [TOP_STRUT_TEX + c + BOTTOM_STRUT_TEX for c in cells]
    return " & ".join(cells) + " \\\\" + trailing_ws


def _add_row_lines(m: re.Match) -> str:
    # Every body row is one physical line ending in " \\" (guaranteed by
    # --wrap=none -- without it Pandoc line-wraps the .tex source itself
    # at ~72 columns, which would make a single logical row span several
    # physical lines and break this). \hline after each one draws the
    # horizontal rule between body rows that booktabs' style never has;
    # _pad_body_row_cells adds the first/last column padding described
    # above.
    lines = m.group(1).split("\n")
    out = []
    for line in lines:
        if line.rstrip().endswith("\\\\"):
            line = _pad_body_row_cells(line)
        out.append(line)
        if line.rstrip().endswith("\\\\"):
            out.append("\\hline")
    return "\\endlastfoot\n" + "\n".join(out) + "\\end{longtable}"


def add_table_grid_lines(tex: str) -> str:
    """Patch every table in Pandoc-generated LaTeX (see COMBINED_TEX) so
    it draws a full grid -- vertical rules between columns and a
    horizontal rule between every row -- matching the source .docx's own
    table style, instead of Pandoc's hardcoded booktabs style (see the
    module-level comment above for exactly what's being matched and
    why)."""
    tex = LONGTABLE_COLSPEC_RE.sub(_add_grid_colspec, tex)
    tex = tex.replace("\\toprule\\noalign{}", "\\hline")
    tex = tex.replace("\\midrule\\noalign{}", "\\hline")
    tex = tex.replace("\\bottomrule\\noalign{}", "\\hline")
    tex = ROW_BLOCK_RE.sub(_add_row_lines, tex)
    return tex


# Column headings are bold in every table in the source .docx (its own
# table style bolds the header row regardless of content), and the
# website reproduces that for free -- a plain HTML <table>'s <th> cells
# render bold by default in every browser, Material's CSS included, no
# matter what the Markdown source says. Pandoc's LaTeX writer has no such
# built-in: it renders a header row exactly like a body row except for
# the rule above/below it, so a table only comes out bold in the PDF if
# its header cells happen to carry their own literal `**bold**` Markdown.
# That in turn only happened for the ~28 of 44 tables (see docs/*.md)
# where convert.py's docx->Markdown pass preserved bold *character*
# formatting Word had on that specific text -- i.e. whether the original
# author happened to manually bold the header row's text on top of the
# table style, not the table style itself, which Pandoc's docx reader has
# no way to carry over as it's a table-level property, not a text run's.
# Forcing it at PDF build time (rather than adding `**...**` to the other
# 16 tables' Markdown) keeps docs/*.md focused on content -- the website
# never needed it -- the same reasoning as every other PDF-only cosmetic
# fix in this script.
#
# Pandoc's LaTeX writer wraps a cell's content in a `minipage` only for
# header rows (confirmed by hand against the generated .tex -- body-row
# cells are always bare text/inline markup, directly in the table cell,
# never minipage-wrapped), which makes minipage a reliable, header-only
# anchor: no separate detection of "is this the header row" is needed,
# and nothing here can mistakenly bold a body cell. bold_table_headers
# piggybacks the header row's vertical-alignment fix and first/last-
# column padding on this same anchor -- see _process_header_cell -- since
# both are also header-row-only concerns.
HEADER_BLOCK_RE = re.compile(r"(?<=@\{\}\}\n).*?\\endhead", re.S)
HEADER_CELL_RE = re.compile(
    r"(\\begin\{minipage\}\[b\]\{\\linewidth\}\\raggedright\n)(.*?)(\n\\end\{minipage\})", re.S
)


def _process_header_cell(m: re.Match, *, first: bool, last: bool) -> str:
    # Also switches each header cell's minipage from Pandoc's own [b]
    # (bottom) alignment to [t] (top). Confirmed by hand this has nothing
    # to do with add_table_grid_lines or any other patch in this file: [b]
    # simply doesn't reliably bottom-align a lone minipage inside a plain
    # longtable cell here (no known reason found -- xelatex gives no
    # warning either), so whenever one header in a row wraps to more lines
    # than its neighbors (e.g. "JSON Name" over "Type"), the shorter
    # neighbors silently render as if top-aligned regardless of the [b] in
    # the source. Forcing [t] everywhere sidesteps the unreliable case
    # entirely rather than relying on it, and matches what most cells were
    # already doing anyway.
    #
    # The first/last column's own \tabcolsep padding (see the comment
    # above _add_grid_colspec) also has to be added here, inside the
    # minipage right after \raggedright/right before \end{minipage}, for
    # the same reason [b] itself is unreliable: putting it instead in the
    # column type's own >{...}/<{...} hook -- which would otherwise be
    # the simpler, one-place fix, and is exactly what body rows still use
    # (see _pad_body_row_cells) -- silently broke this cell's [t]/[b]
    # alignment too, confirmed by hand: a lone minipage sitting right
    # after other (non-empty) material in the same table cell doesn't
    # reliably honor its own vertical alignment in this longtable setup.
    # LEFT_INSET_TEX/RIGHT_INSET_TEX (see _pad_body_row_cells) rather than
    # a plain \hspace, for the same wrap-consistency reason: a header
    # whose own title wraps (e.g. a first/last-column heading two words
    # long) would otherwise only get the indent on its first line.
    #
    # TOP_STRUT_TEX/BOTTOM_STRUT_TEX (see _pad_body_row_cells) apply to
    # every header cell regardless of first/last, unlike the horizontal
    # insets -- vertical padding is a top/bottom-of-row concern, not an
    # outer-edge one, and every header in this document has a neighbor
    # somewhere that can force its row taller (e.g. "JSON Name" wrapping
    # to two lines next to single-line neighbors), so no cell can assume
    # it'll always be the row's shortest.
    opening = m.group(1).replace("[b]", "[t]", 1)
    if first:
        opening += LEFT_INSET_TEX
    opening += TOP_STRUT_TEX
    text = "\\textbf{" + m.group(2) + "}" + BOTTOM_STRUT_TEX
    if last:
        text += RIGHT_INSET_TEX
    return opening + text + m.group(3)


def bold_table_headers(tex: str) -> str:
    """Force every table's header row bold in the PDF (see module-level
    comment above for why it's otherwise inconsistent), fix its vertical
    alignment, and pad its first/last column (see _process_header_cell).
    Runs before add_table_grid_lines, against Pandoc's unpatched
    colspec-close marker (`@{}}` -- add_table_grid_lines only inserts its
    own grid `|` there afterwards), so the two patches stay independent of
    each other's output shape."""

    def process_block(m: re.Match) -> str:
        block = m.group(0)
        cells = list(HEADER_CELL_RE.finditer(block))
        n = len(cells)
        pieces = []
        last_end = 0
        for i, cm in enumerate(cells):
            # A cell is first/last *in its own row* (there are two header
            # row copies in this block, one for \endfirsthead and one for
            # \endhead -- see the module comment above), not first/last in
            # the whole block: either it's the very first/last cell here,
            # or the text right before/after it (up to the next cell)
            # contains that row's own "\\" row-end command.
            is_first = i == 0 or "\\\\" in block[cells[i - 1].end():cm.start()]
            is_last = i == n - 1 or "\\\\" in block[cm.end():cells[i + 1].start()]
            pieces.append(block[last_end:cm.start()])
            pieces.append(_process_header_cell(cm, first=is_first, last=is_last))
            last_end = cm.end()
        pieces.append(block[last_end:])
        return "".join(pieces)

    return HEADER_BLOCK_RE.sub(process_block, tex)


# Every diagram becomes its own `\begin{figure}...\end{figure}` (Pandoc's
# implicit_figures extension, triggered by an image alone in its own
# Markdown paragraph -- true for every image in docs/*.md). Left with no
# placement specifier, Pandoc's template defaults it to `\fps@figure{htbp}`
# -- LaTeX is still free to *defer* an oversized figure to the top of
# whatever page it lands on, and to stack it there with another deferred
# figure, with no check that the two combined still fit within
# \textheight. Confirmed by hand: Figure 8 and Figure 9 in this document
# (two large diagrams separated only by a short paragraph) were deferred
# and stacked this way, producing a page 333.9pt taller than \textheight
# -- silently clipping most of Figure 8 off the bottom of its page, with
# only an "Overfull \vbox" warning in the log, no error, nothing to
# signal a figure had gone missing from the visible document. Rewriting
# every `\begin{figure}` to `\begin{figure}[H]` (the `float` package's
# placement, loaded in pdf-figure-fix.tex) pins each one exactly where it
# falls in reading order -- ordinary page breaking then either fits it in
# the remaining space on the current page or bumps it, whole, to the
# next, but can never combine it with a neighboring figure. That alone
# doesn't cover a single oversized figure whose image, scaled to exactly
# fill \textheight, still overflows the page by its own caption's height
# -- pdf-figure-fix.tex separately redefines \pandocbounded to reserve
# room for that, so the two fixes together mean no image, alone or
# next to another, can land on a page without fitting in full.
FIGURE_BEGIN_RE = re.compile(r"\\begin\{figure\}")


def force_figure_placement(tex: str) -> str:
    return FIGURE_BEGIN_RE.sub(r"\\begin{figure}[H]", tex)


# Pandoc's own `--pdf-engine=xelatex` wrapper (bypassed here -- see
# COMBINED_TEX) runs the engine repeatedly until the log stops asking for
# a rerun. Doing that properly means parsing xelatex's log after every
# pass; empirically, 3 fixed passes reliably stabilizes this document's
# page numbers, the "Table of contents" page numbers, and every hyperref
# cross-reference/bookmark (confirmed by hand: a 4th pass changes
# nothing further).
XELATEX_PASSES = 3


def run_xelatex(tex_path: Path) -> Path:
    # Pandoc writes plain relative image paths into \includegraphics
    # (e.g. "assets/images/foo.png") and normally resolves them itself
    # via --resource-path when producing a PDF directly; driving xelatex
    # separately here means it has to resolve those same relative paths
    # on its own at compile time instead. TEXINPUTS (kpathsea's own
    # search path, also used for graphics files) covers it -- BUILD
    # first for rasterized SVGs (see rasterize_svgs), DOCS second for
    # native PNGs, matching --resource-path's own search order below.
    # The trailing ";" appends (rather than replaces) xelatex's normal
    # default search path, so its own style/class files are still found.
    env = {**os.environ, "TEXINPUTS": f"{BUILD};{DOCS};"}
    log_path = tex_path.with_suffix(".log")
    for _ in range(XELATEX_PASSES):
        proc = subprocess.run(
            [
                "xelatex",
                "-interaction=nonstopmode",
                f"-output-directory={BUILD}",
                str(tex_path),
            ],
            cwd=BUILD, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        if proc.returncode != 0:
            # xelatex's own useful error output goes to the .log file,
            # not stdout/stderr -- surface its tail rather than just the
            # bare exit-code CalledProcessError would raise.
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else "(no log file)"
            raise RuntimeError(f"xelatex failed (exit {proc.returncode}) on {tex_path}:\n{log_tail}")
    return tex_path.with_suffix(".pdf")


# The source .docx itself renders these pages in landscape orientation
# (confirmed via the one w:orient="landscape" sectPr in word/document.xml,
# whose scope ends right after this table's "unused bytecodes" notice) --
# its 11 columns need the extra width. docs/ itself stays orientation-
# agnostic (this would be meaningless markup on the website/GitHub), so
# the PDF build wraps just this page in a raw-LaTeX `landscape` block.
LANDSCAPE_PAGES = {"appendices/c-calculated-point-code/01-operation-codes.md"}

# This table (11 columns) is uniquely tight among all 44 tables in this
# document: set_pipe_table_widths's own width-floor logic (see its
# comment on the same table) already has to scale several columns down
# simultaneously just to fit at \normalsize, leaving essentially no slack
# anywhere -- confirmed by hand that both ways of giving the grid rule's
# first/last-column padding (see add_table_grid_lines) somewhere to come
# from, a fixed amount taken from just those two columns' own width or the
# same total spread thinly across every column's shared budget, broke a
# *different* column in this specific table (its first column's
# "Operation", or one of its several other already floor-bound columns,
# respectively) rather than fixing it. Shrinking only this table's own
# font instead gives every column proportionally more room without
# touching any column's width formula at all, and doesn't need any
# equivalent per-column exception in set_pipe_table_widths's floor
# calculation, which already assumes a deliberately generous per-
# character width (see CHAR_WIDTH_PT) that's now just extra headroom
# rather than exactly what a normal-sized column needed.
OPERATION_CODES_TABLE_RE = re.compile(r"(\| Operation Code \|.*?\n: Operation codes\n)", re.S)


def shrink_operation_codes_table(content: str) -> str:
    return OPERATION_CODES_TABLE_RE.sub(
        lambda m: "```{=latex}\n\\footnotesize\n```\n\n" + m.group(1) + "\n```{=latex}\n\\normalsize\n```\n",
        content,
    )

# docs/index.md doubles as the MkDocs Home page and the PDF cover -- fine
# as plain markdown on the website, but letting it flow through pandoc's
# normal pipeline for the PDF breaks two ways: (1) pandoc's own title+TOC
# machinery (`--metadata title`, `--toc`) always renders *before* the
# body, so this file's own "document name/version/date" content -- being
# the first body file -- ends up sandwiched between them rather than
# being page 1, and (2) its logo image, alone in its own paragraph, trips
# pandoc's implicit_figures extension and gets an unwanted "Figure 1: ..."
# numbered caption, same as the diagrams elsewhere in the document (which
# *do* want that numbering). Both are avoided by special-casing this one
# file into a hand-built LaTeX titlepage (raw \includegraphics sidesteps
# implicit_figures) instead of letting it fall through normalize_paths
# like every other page -- see cover_page_latex().
COVER_FILE = "index.md"
COVER_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
COVER_TITLE_RE = re.compile(r"\*\*(.+?)\*\*")
COVER_VERSION_RE = re.compile(r"(?m)^Version:\s*(.*)$")
COVER_DATE_RE = re.compile(r"(?m)^Date:\s*(.*)$")


def cover_page_latex(docs: Path) -> str:
    content = (docs / COVER_FILE).read_text(encoding="utf-8")
    image_path = str((docs / COVER_IMAGE_RE.search(content).group(1)).resolve()).replace("\\", "/")
    title = COVER_TITLE_RE.search(content).group(1)
    version = COVER_VERSION_RE.search(content).group(1)
    date = COVER_DATE_RE.search(content).group(1)
    return "\n".join([
        "```{=latex}",
        # The cover has no footer at all in the source .docx (its own
        # titlepage-equivalent section carries only a bare "Public" mark --
        # see footer3.xml). \thispagestyle{empty} (titlepage's own default)
        # doesn't stop eso-pic's hooks, which fire every page regardless of
        # \pagestyle -- see footer_fix_tex -- so this needs its own switch.
        "\\lucidsuppressfooter",
        "\\begin{titlepage}",
        "\\begin{center}",
        "\\vspace*{2cm}",
        f"\\includegraphics[width=0.45\\textwidth]{{{image_path}}}",
        "\\end{center}",
        "\\vspace{4cm}",
        "\\raggedright",
        f"{{\\Large\\bfseries {title}\\par}}",
        "\\vfill",
        f"Version: {version}\\\\",
        f"Date: {date}",
        "\\end{titlepage}",
        "\\lucidrestorefooter",
        "```",
    ])


# The original .docx has no Word-generated table of contents until *after*
# all of the front matter (Disclaimer through References) -- confirmed by
# where "Table of contents" actually sits in the source .pdf, page 5-6,
# right before "1 Introduction" on page 11, not up front by the cover.
# Pandoc's `--toc` flag, by contrast, always renders the TOC immediately
# after the title/cover and before the first body content, which would
# put it before Disclaimer instead. So `--toc` is left off the pandoc
# invocation entirely, and \tableofcontents is injected here instead, as
# raw LaTeX right after the last front-matter file -- its content isn't
# affected by *where* it's placed (LaTeX's .toc mechanism collects every
# heading in the document regardless), only the page it prints on.
TOC_AFTER_FILE = "front-matter/references.md"
# The source .docx also gives the TOC its own page in both directions --
# References ends with the rest of its page blank rather than running
# straight into "Table of contents", and the TOC's own last page likewise
# ends with room to spare rather than "1 Introduction" continuing on it.
# Neither `\tableofcontents` nor the surrounding raw-LaTeX block causes a
# page break on its own (unlike `\chapter` in the `report`/`book` classes,
# `article`'s sectioning commands -- including this document's own
# `\appendix`-adjacent ones -- never force one), so both are explicit here.
TOC_LATEX = "\n".join([
    "```{=latex}",
    "\\clearpage",
    "\\renewcommand*\\contentsname{Table of contents}",
    "\\tableofcontents",
    "\\clearpage",
    "```",
])

# The source .docx numbers its four appendix headings A-D (a separate
# upperLetter numbering level tied to the "Appendix H1" style, confirmed
# in word/numbering.xml), not as a continuation of the body's 1, 2, 3...
# sequence -- matching the "Appendix B"/"Appendix C" cross-references
# already in the body text. docs/*.md itself carries no heading numbers
# anywhere (the website never numbers headings; --number-sections is a
# PDF-only concern) -- LaTeX's own \appendix command is the equivalent
# switch, so it's injected here rather than baked into the Markdown.
FIRST_APPENDIX_FILE = "appendices/a-json-files.md"


def main():
    if not DOCS.exists():
        sys.exit(
            f"{DOCS} doesn't exist -- run scripts/fetch_spec.py first to "
            "fetch the spec content this builds from."
        )

    nav = yaml.safe_load(MKDOCS_YML.read_text(encoding="utf-8"))["nav"]
    files = nav_files(nav)

    BUILD.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Clear the previous outputs up front, so a failed build leaves dist/
    # with no PDF rather than a stale one the website build would then
    # pick up and link to (see mkdocs_table_captions.py's on_files).
    for stale in (OUT_PDF, OUT_BUILD_INFO):
        stale.unlink(missing_ok=True)
    rasterize_svgs()

    parts = []
    for out_rel in files:
        if out_rel == COVER_FILE:
            parts.append(cover_page_latex(DOCS))
            continue
        content = normalize_paths((DOCS / out_rel).read_text(encoding="utf-8"))
        content = set_pipe_table_widths(content)
        content = convert_table_line_breaks(content)
        content = add_section_page_breaks(content)
        if out_rel == FIRST_APPENDIX_FILE:
            parts.append("```{=latex}\n\\appendix\n```")
        if out_rel in LANDSCAPE_PAGES:
            content = shrink_operation_codes_table(content)
            content = (
                "```{=latex}\n\\begin{landscape}\n\\lucidenablelandscapefooter\n```\n\n"
                f"{content}\n\n"
                "```{=latex}\n\\end{landscape}\n\\luciddisablelandscapefooter\n```"
            )
        parts.append(content)
        if out_rel == TOC_AFTER_FILE:
            parts.append(TOC_LATEX)
    COMBINED_MD.write_text("\n\n".join(parts), encoding="utf-8")

    version, date, copyright_years = read_footer_fields(DOCS)
    spec_info = read_spec_version()
    PDF_FOOTER_FIX_TEX.write_text(
        footer_fix_tex(DOCS, version, date, copyright_years, spec_info), encoding="utf-8"
    )

    subprocess.run(
        [
            "pandoc",
            str(COMBINED_MD),
            "-o", str(COMBINED_TEX),
            "--standalone",
            "--number-sections",
            f"--syntax-highlighting={CODE_HIGHLIGHT_STYLE}",
            "-V", "title-meta=Lucid Protocol Specification",
            "-V", "papersize=a4",
            "-V", "geometry:margin=15mm",
            # footer_fix_tex draws the footer itself via absolute eso-pic
            # placement (not geometry's footskip band -- see that function),
            # up to 14mm+rule from the bottom edge; the plain 15mm bottom
            # margin above leaves body text no clearance above that, so
            # it's overridden wider here specifically.
            "-V", "geometry:bottom=25mm",
            "-V", "mainfont=Montserrat",
            "-V", f"mainfontoptions=Path={MONTSERRAT_DIR.as_posix()}/",
            "-V", "mainfontoptions=UprightFont=Montserrat-Regular.ttf",
            "-V", "mainfontoptions=BoldFont=Montserrat-Bold.ttf",
            "-V", "mainfontoptions=ItalicFont=Montserrat-Italic.ttf",
            "-V", "mainfontoptions=BoldItalicFont=Montserrat-BoldItalic.ttf",
            # Word's "single" line spacing is a known misnomer -- it's
            # actually a ~1.08x multiplier of nominal font size, not a
            # literal 1.0 (Word has rendered it this way since the
            # Office 2007 line-spacing default changed; the source .docx
            # never overrides it for body text -- see word/styles.xml).
            # LaTeX's own single-spacing default is a true 1.0, part of
            # why the plain-Latin-Modern PDF felt more cramped line-to-
            # line than the original beyond the font swap alone.
            "-V", "linestretch=1.08",
            "-V", "header-includes=\\usepackage{pdflscape}",
            f"--include-in-header={PDF_HEADING_FIX_TEX}",
            f"--include-in-header={PDF_FIGURE_FIX_TEX}",
            f"--include-in-header={PDF_FOOTER_FIX_TEX}",
            f"--include-before-body={PDF_BOOKMARKS_TEX}",
            f"--resource-path={BUILD}{';' if sys.platform == 'win32' else ':'}{DOCS}",
            "--wrap=none",
        ],
        check=True,
    )
    tex = bold_table_headers(COMBINED_TEX.read_text(encoding="utf-8"))
    tex = force_figure_placement(tex)
    COMBINED_TEX.write_text(add_table_grid_lines(tex), encoding="utf-8")

    built_pdf = run_xelatex(COMBINED_TEX)
    shutil.copyfile(built_pdf, OUT_PDF)
    print(f"Wrote {OUT_PDF}")

    if spec_info is not None:
        build_info = {**spec_info, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        OUT_BUILD_INFO.write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {OUT_BUILD_INFO}")


if __name__ == "__main__":
    sys.exit(main())
