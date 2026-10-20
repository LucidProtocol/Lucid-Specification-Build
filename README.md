# Lucid Protocol Specification -- build tooling

This repository builds the [Lucid Protocol
specification](https://github.com/LucidProtocol/Lucid-Specification) into
two things:

- **A website**, via [MkDocs](https://www.mkdocs.org/) with the
  [Material](https://squidfunk.github.io/mkdocs-material/) theme.
- **A single PDF**, by concatenating the same Markdown files (in the same
  order) and running them through [Pandoc](https://pandoc.org/)/xelatex.

It contains no spec content itself -- no Markdown, JSON, or JSON Schema.
That all lives in the public
[Lucid-Specification](https://github.com/LucidProtocol/Lucid-Specification)
repo, and `scripts/fetch_spec.py` fetches it into `lucid-specification/`
(gitignored) before either build runs. `mkdocs.yml`'s `nav:` is the one
place page order/titles are defined; `scripts/build_pdf.py` reads that
same `nav:` so the site and the PDF can never drift out of sync with each
other.

## Layout

```
lucid-specification/          fetched by fetch_spec.py -- not in this repo
  docs/                       the spec's Markdown source
assets/
  favicon.png, favicon.ico     site icon, the {l} cut from the spec's lucid-wordmark.png
scripts/
  fetch_spec.py                fetches/updates lucid-specification/
  build_pdf.py                 builds dist/lucid-protocol-specification.pdf
  mkdocs_table_captions.py     MkDocs hooks: PDF-matching section/table/figure numbers, captions and note boxes, footer provenance, PDF link
  pdf-code-block.theme         Pandoc syntax-highlighting theme for the PDF's code blocks
  pdf-heading-fix.tex          Fixes level-4 headings running in to their first paragraph
  pdf-figure-fix.tex           Keeps every diagram whole on one page
  pdf-bookmarks.tex            Numbers the PDF's sidebar bookmarks
  fonts/Montserrat/            Vendored PDF body/heading font
mkdocs.yml                     site nav / theme config / hooks / extra_css
requirements.txt               mkdocs + mkdocs-material
spec-version.json              written by fetch_spec.py -- which spec commit is checked out
```

## Setup

Assumes Python 3 is already installed. Also needed: git, Pandoc, and a
LaTeX distribution with `xelatex` (for the PDF build only -- the website
doesn't need either Pandoc or LaTeX).

### Windows
(via [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/), built into Windows 10/11 -- or download installers from
[pandoc.org](https://pandoc.org/installing.html) and [miktex.org](https://miktex.org/) instead):

```
winget install --id JohnMacFarlane.Pandoc
winget install --id MiKTeX.MiKTeX
winget install --id Git.Git
```

pip is bundled with the standard python.org/Microsoft Store installers --
nothing extra to install. Confirm with `py -m pip --version`.

MiKTeX installs only a minimal set of LaTeX packages up front and
downloads the rest the first time a document needs them. By default it
asks before each one, so the first PDF build on a new machine pops up a
series of "install package?" dialogs (about 14 for this document, e.g.
`unicode-math.sty`, `xurl.sty`, `upquote.sty`, `latinmodern-math.otf`)
that each need clicking through. Accepting them all works fine, but to
let MiKTeX install them without asking, run this once after installing
it (in a new terminal, so `initexmf` is on your PATH):

```
initexmf --set-config-value=[MPM]AutoInstall=1
```

The same setting is in the MiKTeX Console under Settings → "Always
install missing packages on-the-fly".

### macOS
(via [Homebrew](https://brew.sh/)):

```
brew install pandoc git
brew install --cask mactex
```

MacTeX (not the smaller BasicTeX) is deliberately what's recommended here
-- BasicTeX is missing several packages this build needs, which would
otherwise mean manually tracking each one down via `tlmgr install` one at
a time. It's a large download (~4GB) but a one-time cost. pip is bundled
with the python.org installer and Homebrew's own python formula; confirm
with `python3 -m pip --version`.

### Linux (Debian/Ubuntu)

```
sudo apt update
sudo apt install pandoc texlive-xetex texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended lmodern git python3-pip python3-venv
```

TeX Live on Linux never offers to install a missing LaTeX package the
way MiKTeX does -- the build just stops with `! LaTeX Error: File
'xurl.sty' not found` -- so every package this document uses has to be
installed up front. `texlive-latex-extra` is the one that's easy to miss:
it carries `xurl`, among others. (`texlive-full` would also work, but
it's several GB.)

The PDF build also needs a Chrome, Chromium or Edge browser on the PATH
(`google-chrome`, `chromium`, ...) to turn the spec's SVG diagrams into
images -- see `find_browser` in `scripts/build_pdf.py`.

Unlike Windows/macOS, Debian/Ubuntu's system Python doesn't bundle pip --
that's why `python3-pip` is listed explicitly above rather than assumed.

### Then, on any platform
Create a virtual environment and install the
Python dependencies (mkdocs + mkdocs-material; PyYAML comes along as a
dependency of mkdocs itself):

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
(and use `.venv/bin/python` instead of `.venv\Scripts\python` in every
command below).

### Optional: GitHub CLI (`gh`)

Not needed for either build. It's useful for inspecting this repo's
GitHub Actions runs from the command line -- e.g. reading a failed run's
logs with `gh run view --log-failed` -- without opening the GitHub
website.

Install it:

- **Windows:** `winget install --id GitHub.cli`
- **macOS:** `brew install gh`
- **Linux (Debian/Ubuntu):** `sudo apt install gh` -- older releases
  don't carry it, in which case follow GitHub's [Linux install
  instructions](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)
  to add their apt repository first.

Then open a new terminal (so `gh` is on your PATH) and log in:

```
gh --version
gh auth login
```

Choose **GitHub.com**, **HTTPS**, and **Login with a web browser**. It
shows a one-time code to paste into the browser page that opens. If the
LucidProtocol organisation uses SSO, GitHub may also ask you to
authorise the login for that organisation -- approve it. Confirm with:

```
gh auth status
```

Any tool already running when `gh` was installed (an editor, Claude
Code, etc.) needs restarting before it can find `gh` on the PATH.

## Fetching the spec content

```
.venv\Scripts\python scripts\fetch_spec.py               # latest commit on the default branch
.venv\Scripts\python scripts\fetch_spec.py --ref v1.4     # a specific tag or commit instead
.venv\Scripts\python scripts\fetch_spec.py --ref origin/spec-build   # a branch other than the default
```

A branch needs the `origin/` prefix: the checkout only has the remote's
branches, never a local copy of them.

This clones (first run) or updates (later runs) `lucid-specification/`
and records what's checked out in `spec-version.json` -- commit hash,
tag (if any), and commit date. Re-run it whenever you want to build
against newer spec content; it always discards any local state in
`lucid-specification/` first, since that folder only ever exists to be
built from.

## Building the website

To create the static web documents in site/ use the following command:

```
.venv\Scripts\python -m mkdocs build
```

To build and serve a live preview at localhost:8000 use the following command:

```
.venv\Scripts\python -m mkdocs serve
```

The site footer shows which Lucid-Specification commit/tag it was built
from (from `spec-version.json`, via an `on_config` hook in
`scripts/mkdocs_table_captions.py`).

If `dist/lucid-protocol-specification.pdf` exists when the site is built,
it's copied into `site/` and a "Download PDF" link is added at the end of
the nav. So to get a site with the PDF included, run `build_pdf.py`
(below) first. Without it, the site builds fine, just with no PDF link.
The PDF is only included if it was built from the same spec commit
that's checked out now (`dist/build-info.json` against
`spec-version.json`). Otherwise the build warns "Not linking ...
Rebuild it with build_pdf.py" and leaves the link out, and `--strict`
stops the build. That way the site never links to a PDF of a different
spec version.

## Deploying the website

`site/` (from `mkdocs build`) is a self-contained folder of static files
-- there's no server-side processing involved, so "deploying" always
just means getting that folder's *contents* onto whatever serves it.
There's no fixed folder it has to land in: every page-to-page link and
every CSS/JS asset reference MkDocs generates is relative to that page's
own depth in the tree (confirmed by inspecting a built page --
`href="../../front-matter/preface/"`, not an absolute `/front-matter/
preface/`), specifically so the same `site/` output works whether it's
dropped at a domain root, a subdirectory, or a subdomain with no rebuild
needed. The one thing that *is* absolute is SEO-only metadata -- each
page's `<link rel="canonical">` tag and every URL in `sitemap.xml` are
built verbatim from `mkdocs.yml`'s `site_url`, so that's worth setting to
wherever it actually ends up (before building) for search engines'
benefit, even though navigation itself doesn't depend on it.

- **A separate server/VPS you manage** -- copy `site/`'s contents to
  wherever that server's web root (or a subdirectory of it) is, e.g.:

  ```
  rsync -av --delete site/ user@host:/var/www/lucidprotocol/spec/
  ```

  (`scp -r site/* user@host:/var/www/lucidprotocol/spec/` works too, just
  without rsync's ability to also delete files removed from a previous
  build.)

- **Managed WordPress hosting** (WP Engine, Kinsta, SiteGround, cPanel,
  etc.) -- upload `site/`'s contents via SFTP or the host's File Manager,
  either into a subdirectory of the existing site or via an "addon
  domain"/subdomain the hosting panel lets you create. Exact steps vary
  by host; the underlying idea is the same as the self-managed case
  above, just through their panel instead of raw SSH/rsync.

- **WordPress.com (fully hosted, no file access)** -- there's no way to
  place files alongside the WordPress install directly. The practical
  option is a subdomain (e.g. `docs.lucidprotocol.org`) hosted somewhere
  else entirely -- GitHub Pages (see below) is the natural fit -- with a
  DNS record for that subdomain pointed at it. This needs no cooperation
  from WordPress.com at all, just a DNS change wherever the domain itself
  is managed.

- **GitHub Pages** -- `mkdocs gh-deploy` builds the site and pushes the
  result straight to a `gh-pages` branch, which GitHub then serves
  automatically:

  ```
  .venv\Scripts\python -m mkdocs gh-deploy
  ```

  This publishes from *this* build repo. The intended home for the
  public site is GitHub Pages on the Lucid-Specification repo instead,
  built and deployed automatically by a GitHub Actions workflow there.
  `gh-deploy` from here remains useful for a quick manual preview.

## Building the PDF

```
.venv\Scripts\python scripts\build_pdf.py
```

Outputs `dist/lucid-protocol-specification.pdf`, plus
`dist/build-info.json` recording the same spec commit/tag/date that's
stamped into the PDF's own footer (`Version: ... | spec <tag-or-hash>`).
It deletes both files when it starts, so a failed build leaves no PDF in
`dist/`, not an out-of-date one.

## Provenance

Because `lucid-specification/` can be re-fetched at any tag/commit, a
given PDF or website build is only traceable back to the spec content it
was built from via this recorded state, not via this repo's own git
history. `spec-version.json` (present after any `fetch_spec.py` run) and
`dist/build-info.json` (written by every `build_pdf.py` run) both carry:

```json
{
  "remote": "https://github.com/LucidProtocol/Lucid-Specification.git",
  "ref_requested": "latest",
  "commit": "<full sha>",
  "commit_short": "<short sha>",
  "tag": null,
  "committed_at": "<ISO 8601>",
  "fetched_at": "<ISO 8601>"
}
```

## Future work: moving off MkDocs 1.x

The website is built with MkDocs 1.x, and `requirements.txt` pins
`mkdocs<2` on purpose. MkDocs 2.0 is a rewrite that removes the plugin
and hook system this repo's `scripts/mkdocs_table_captions.py` relies on,
switches `mkdocs.yml` to a TOML config, and isn't supported by the
Material theme. The pinned versions keep working after 2.0 is released,
so nothing breaks, but Material for MkDocs only gets critical fixes
until **5 May 2027**. Its team's recommended successor is
[Zensical](https://zensical.org/), meant as a drop-in replacement that
builds existing MkDocs 1.x projects, `mkdocs.yml` included.

Plan:

- **By March 2027:** on a branch, try building the site with Zensical
  in place of MkDocs. The main thing to check is whether it runs this
  repo's hooks (table captions, footer provenance, and the Download PDF
  link).
- **If it doesn't:** move the hooks' work into a small script run after
  `fetch_spec.py`, so the site generator only ever sees plain Markdown.
- **Before 5 May 2027:** switch over, update `requirements.txt` and the
  GitHub Actions workflow, and update this README.

Background: Material's write-up,
[MkDocs 2.0](https://squidfunk.github.io/mkdocs-material/blog/2026/02/18/mkdocs-2.0/).
