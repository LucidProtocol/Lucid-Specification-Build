# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repository is build tooling only. It contains no Lucid Protocol
specification content -- no Markdown, JSON, or JSON Schema -- and never
will: that all lives in the public
[Lucid-Specification](https://github.com/LucidProtocol/Lucid-Specification)
repo. `scripts/fetch_spec.py` fetches it into `lucid-specification/`
(gitignored, not part of this repo) before either build below runs. From
that fetched content, this repo builds:

- **A website**, via [MkDocs](https://www.mkdocs.org/) with the
  [Material](https://squidfunk.github.io/mkdocs-material/) theme.
- **A single PDF**, by concatenating the same Markdown files (in the same
  order) and running them through [Pandoc](https://pandoc.org/)/xelatex.

There is no test suite, linter, or CI config in this repo --
"development" here means editing `scripts/build_pdf.py`, `mkdocs.yml`, or
the other files under `scripts/`, then rebuilding to check the result.
Editing the spec *content* itself happens in the Lucid-Specification
repo, not here.

## Commands

Setup (Python 3, git, [Pandoc](https://pandoc.org/installing.html), and a
LaTeX distribution with `xelatex`, e.g. MiKTeX or TeX Live, are required
-- see README.md for per-OS install commands):

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Fetch (or update) the spec content this builds from:

```
.venv\Scripts\python scripts\fetch_spec.py               # latest commit on the default branch
.venv\Scripts\python scripts\fetch_spec.py --ref v1.4     # a specific tag or commit instead
.venv\Scripts\python scripts\fetch_spec.py --ref origin/spec-build   # a branch (needs the origin/ prefix)
```

Build/preview the website (run the PDF build first if the site should
include the PDF and its "Download PDF" nav link):

```
.venv\Scripts\python -m mkdocs serve      # live preview at localhost:8000
.venv\Scripts\python -m mkdocs build --strict   # outputs to site/; --strict catches broken nav/links
```

Build the PDF (outputs `dist/lucid-protocol-specification.pdf`):

```
.venv\Scripts\python scripts\build_pdf.py
```

Both build commands fail fast with a clear message if `fetch_spec.py`
hasn't been run yet (`lucid-specification/docs/` won't exist).

## Architecture

**This repo owns no spec content, only the tooling that turns it into a
PDF/website.** `scripts/fetch_spec.py` clones (first run) or
resets-and-re-fetches (later runs) the public Lucid-Specification repo
into `lucid-specification/` -- always discarding whatever was there
first, since that folder only ever exists to be built from, never edited
directly. `--ref <tag-or-commit>` checks out something other than the
latest default-branch commit; with no `--ref`, it resolves the remote's
own default branch via `git remote set-head origin --auto` rather than
assuming a branch name. Either way it records what's checked out --
commit hash (full + short), tag if the commit has one, and commit date --
in `spec-version.json` at this repo's root (gitignored, regenerated on
every fetch).

**Provenance flows from `spec-version.json` into both build outputs**,
since `lucid-specification/` can be re-fetched at any tag/commit at any
time -- without this, a given PDF or website build would only be
traceable back to the exact spec content it came from by chance, not by
design. `build_pdf.py`'s `read_spec_version()` reads it (returning `None`
gracefully if `fetch_spec.py` hasn't been run, so a missing file degrades
the footer rather than blocking the build) and threads a `spec {tag-or-
hash}` clause into the PDF's own footer (see below) via `footer_fix_tex`,
then writes the same data plus a build timestamp to `dist/build-
info.json` once the PDF is done. `scripts/mkdocs_table_captions.py`'s
`on_config` hook does the equivalent for the website, setting `mkdocs.yml`
config's native `copyright` value (rendered by Material's footer for
free, no custom theme override needed) from the same file.

**`mkdocs.yml`'s `nav:` is the single source of truth for page order and
titles.** `scripts/build_pdf.py` parses that same `nav:` (via PyYAML) to
decide what order to concatenate `lucid-specification/docs/*.md` files
into the PDF, so the website and PDF can never drift out of sync with
each other. That's also why the website's "Download PDF" nav link isn't
in `nav:`: `scripts/mkdocs_table_captions.py`'s `on_files`/`on_nav` hooks
copy `dist/lucid-protocol-specification.pdf` into the site and append a
nav `Link` to it, only when that file exists (a `nav:` entry for a file
outside `docs_dir` would also fail `--strict`) and `dist/build-info.json`'s
commit matches `spec-version.json`'s -- otherwise they log a warning and
skip it (so `--strict` aborts) rather than link a PDF of a different spec
version. `on_files` also copies `assets/favicon.png` (what
`mkdocs.yml`'s `theme.favicon` points at) and `assets/favicon.ico` (for
the browser's PDF viewer, which requests `/favicon.ico`) to the site
root; both are the `{l}` cut from the spec's `lucid-wordmark.png`.
`build_pdf.py` deletes the previous PDF and `build-info.json`
before building, so a failed PDF build can't leave a stale one behind. `requirements.txt` caps
`mkdocs<2` because MkDocs 2.0 drops the plugin/hook system these hooks
rely on (it also moves config to TOML and changes how themes get the
nav, and Material for MkDocs won't support it -- Material itself caps
`mkdocs<2` too). Material gets critical fixes only until 5 May 2027;
its team's successor is Zensical, meant as a drop-in replacement for
MkDocs 1.x that reads `mkdocs.yml` unchanged. Planned migration (see the
"Future work" section at the end of README.md): try a Zensical build on a branch in
early 2027, mainly to check it runs these hooks (`on_page_markdown`,
`on_config`, `on_files`, `on_nav`). If not, move their work into a
pre-build script run after `fetch_spec.py` (rewrite captions, copy the
PDF into `docs_dir`, add the nav entry), so the site generator only sees
plain Markdown. `docs_dir: lucid-specification/docs` in `mkdocs.yml` and the
`DOCS` constant in `build_pdf.py` both point at the fetched checkout, not
a folder native to this repo.

**`mkdocs.yml`'s `hooks:`/`extra_css:` make the website render two things
the spec's Pandoc-flavored Markdown otherwise gets wrong**, on top of the
provenance footer above. Every table's `: Caption text` line (Pandoc's
own numbered-table-caption syntax -- see the PDF section below for why
the spec's Markdown uses it at all) is plain Pandoc markdown, not
CommonMark/GFM, so MkDocs' Python-Markdown engine (and GitHub's own file
preview) render it completely literally as a stray paragraph starting
with a colon -- `scripts/mkdocs_table_captions.py`'s `on_page_markdown`
hook rewrites it into a styled `<p class="table-caption">`.

The same hook file gives the website the PDF's numbering -- section
numbers on headings, the "on this page" contents and the nav, and
"Table N:"/"Figure N:" captions (figures get a caption paragraph added,
since Python-Markdown shows none). In the PDF those numbers come from
LaTeX's counters, not from the Markdown, so `on_nav` re-counts them the
way LaTeX does: every page in nav order, skipping `build_pdf.COVER_FILE`
and headings with `build_pdf.UNNUMBERED_HEADING_CLASS`, lettering
top-level headings from `build_pdf.FIRST_APPENDIX_FILE` on, one running
count each for captioned tables and for figures (an image alone in its
paragraph with alt text). The hook imports `build_pdf` for those
constants so the two can't drift apart. Numbers are inserted into the
rendered HTML (`on_page_content`), never the Markdown, so heading ids and
links are unchanged; if a page's HTML has a different heading/figure
count from the Markdown scan, it logs a warning (fails `--strict`).
Pages with no H1 get an unnumbered `<h1>` of their nav title, since
Material would otherwise generate one from the now-numbered
`page.title`. The same hook also replaces the 🛈 note symbol with
**NOTE:**, as `build_pdf.py` does, and drops the empty body row
Python-Markdown adds to the spec's one-row callout tables.

Table grid lines (the vertical rules Material's default table style is
missing) come from `lucid-specification/docs/assets/stylesheets/
extra.css`, which arrives with the fetched content rather than living in
this repo, since it has to sit inside whatever `docs_dir` MkDocs is
pointed at to be picked up by `extra_css:` at all.

**The PDF build is two-stage** (Pandoc Markdown → `.tex` via
`--standalone`, patch the `.tex` text, then `build_pdf.py` drives
`xelatex` itself -- `COMBINED_TEX`, `run_xelatex`) instead of Pandoc's
usual single `-o *.pdf` call that drives `xelatex` internally. The reason
is `add_table_grid_lines`: the spec's tables need a full grid (lines
around every cell), but Pandoc's LaTeX table writer hardcodes the
booktabs style -- rules only at the top/bottom/header, never vertical,
never between body rows -- with no flag or template variable to change
it anywhere in Pandoc itself. The only way to get a different table style
is to patch the LaTeX text Pandoc generates before xelatex compiles it,
which means the build has to stop at that intermediate `.tex` file rather
than letting Pandoc go straight to PDF. The patch only has to match the
one exact shape Pandoc's writer emits for a pipe table (every column uses
`>{\raggedright\arraybackslash}`, left-aligned is the only alignment any
table here uses): insert a bare `|` between each column's width spec in
the `longtable` column-spec line for vertical rules, and a `\hline` after
every body row (each one guaranteed to be exactly one physical line by
`--wrap=none`, otherwise Pandoc line-wraps the `.tex` source itself at
~72 columns and a single logical row could span several physical lines).

Plain `\hline` adds no vertical space of its own, so every row's text
would sit flush against the grid rule above/below it --
`TOP_STRUT_TEX`/`BOTTOM_STRUT_TEX` (zero-width `\rule`s with an explicit
height-only/depth-only, inserted as the literal first/last token of every
cell's paragraph) add height/depth to just that cell's own first/last
line, additively rather than as a minimum, so it stacks correctly
regardless of how many lines a cell wraps to or how tall a neighboring
cell in the same row is. The first/last column's own left/right inset
(the `\tabcolsep` gap between text and the outer grid rule) is set via
`\leftskip`/`\rightskip` (paragraph-level glue, applies to every wrapped
line) rather than a one-off `\hspace` (inline content, only ever lands on
one line) -- see `_pad_body_row_cells`/`_process_header_cell`.

`set_pipe_table_widths` sets every pipe table's separator-row dash
lengths proportional to that column's actual cell content, applied to
each file's content right after `normalize_paths`, before concatenation.
GitHub and MkDocs render pipe tables as plain HTML with the browser doing
content-based auto-layout, so dash length is cosmetic there -- but this
script reads the Markdown with Pandoc's `markdown` format (not `gfm`),
since only `markdown` supports the `: Caption` syntax above, and that
reader's LaTeX writer turns relative dash-run length directly into each
column's fixed width fraction. Left uniform, every column comes out
equally wide regardless of content, wrapping a free-text column across
many lines instead of using the page's width where it's actually needed.
Each column also gets a minimum width sized to its single longest
unbreakable token (a header word or a camelCase JSON name) -- a header
cell wider than its column doesn't wrap or warn, it silently overflows
into the next column as illegible overlapping text. When enough columns
in one row need their floor at once, the redistribution only ever takes
width from columns with room above their own floor, falling back to a
uniform scale-down (never negative) if even every column at its floor
doesn't fit in 100%.

`run_xelatex` runs the engine itself 3 times (`XELATEX_PASSES`) --
normally Pandoc's own engine wrapper reruns `xelatex` until its log stops
asking for another pass to resolve cross-references/the TOC/bookmarks,
which this script no longer gets for free since it invokes `xelatex`
directly; 3 fixed passes was confirmed by hand to fully stabilize this
document.

It also passes `--syntax-highlighting=scripts/pdf-code-block.theme` (a
background-only Pandoc theme, matching the shading fenced code blocks get
on the website for free from Material's own CSS); `-V papersize=a4 -V
geometry:margin=15mm` (unset, Pandoc's default template skips loading
`geometry` entirely and falls back to US Letter with ~1.8in margins, too
narrow for some tables); `-V mainfont=Montserrat` plus
`mainfontoptions` pointing at the four static weight files vendored under
`scripts/fonts/Montserrat/` (Montserrat is a Google Font, not reliably
present in any given machine's font database, so it's referenced by
explicit file path rather than relying on OS font discovery) and `-V
linestretch=1.08`; `--include-in-header=scripts/pdf-heading-fix.tex`
(redefines `\paragraph`, what a level-4 Markdown heading becomes, so it
starts its own line instead of the `article` class's default "run-in"
style); `--include-in-header=scripts/pdf-figure-fix.tex` plus patching
every `\begin{figure}` to `\begin{figure}[H]` (pins each diagram exactly
where it falls in reading order and reserves caption headroom in
Pandoc's `\pandocbounded` macro -- without this, two large adjacent
diagrams could get deferred and stacked onto the same page, silently
overflowing past the bottom margin with only an easy-to-miss "Overfull
\vbox" in the log); `--include-before-body=scripts/pdf-bookmarks.tex`
(`\hypersetup{bookmarksnumbered=true}`, so the PDF's sidebar outline
carries the same section numbers as the headings themselves -- can't be a
`header-includes` variable since Pandoc's template loads hyperref *after*
`header-includes` runs).

Every numbered top-level section/appendix starts on a fresh page
(`add_section_page_breaks`, raw-LaTeX `\clearpage` before every H1 that
isn't front matter). The four appendices number A-D via a raw-LaTeX
`\appendix` injected before the first one, rather than continuing the
body's 1, 2, 3... sequence. The Operation Codes table (11 columns) is
wrapped in a raw-LaTeX `landscape` block (`LANDSCAPE_PAGES`, needs `-V
header-includes=\usepackage{pdflscape}`) since the width-floor logic alone
can't give every column room on a portrait page. `rasterize_svgs`
rasterizes the spec's SVG diagrams to PNG (headless Chrome/Edge, since
`rsvg-convert` isn't installed) because the PDF path needs raster images.
`find_browser` checks fixed Windows/macOS install paths first, then
`shutil.which` for the Linux names (`google-chrome`, `chromium`, ...),
which is what GitHub's ubuntu runners need.

The footer (`footer_fix_tex`, `PDF_FOOTER_FIX_TEX` -- generated fresh
each build since its content is data, not a static patch) is built from
eso-pic's absolute page-position hooks
(`\AddToShipoutPictureBG`/`FG`) rather than fancyhdr's `\fancyfoot`,
which turned out to silently drop whichever line ends up last in a
multi-line `\fancyfoot` argument once spread across many pages.
`read_footer_fields` pulls Version/Date from the cover page and the
copyright year from the front-matter preface directly out of the fetched
Markdown, so a future spec revision bump never needs a `build_pdf.py`
edit; `read_spec_version`/`spec-version.json` (see Provenance above) adds
the `spec {tag-or-hash}` clause alongside them.
