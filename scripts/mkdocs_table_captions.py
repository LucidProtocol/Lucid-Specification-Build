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
"""
import json
import re
from pathlib import Path

CAPTION_RE = re.compile(r"(?m)^: (.+)$")
SPEC_VERSION_FILE = Path(__file__).resolve().parent.parent / "spec-version.json"


def on_page_markdown(markdown, **kwargs):
    return CAPTION_RE.sub(r'<p class="table-caption">\1</p>', markdown)


def on_config(config, **kwargs):
    if not SPEC_VERSION_FILE.exists():
        return config
    info = json.loads(SPEC_VERSION_FILE.read_text(encoding="utf-8"))
    label = info["tag"] or info["commit_short"]
    config["copyright"] = f"Built from Lucid-Specification @ {label}"
    return config
