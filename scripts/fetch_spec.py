#!/usr/bin/env python3
"""
Fetch the Lucid Protocol specification content (docs/, JSON, JSON Schema)
this repo builds into a PDF and website, from the public
LucidProtocol/Lucid-Specification repo.

This repo carries no copy of the spec content itself -- only the tooling
that turns it into a PDF/website -- so `lucid-specification/` is gitignored
and populated by this script instead. Run it before `mkdocs build`/`serve`
or `scripts/build_pdf.py`; re-run it any time to pick up the latest spec
content, or pass --ref to build against a specific tag/commit instead.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "lucid-specification"
SPEC_VERSION_FILE = ROOT / "spec-version.json"
SPEC_REMOTE = "https://github.com/LucidProtocol/Lucid-Specification.git"


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def out(*args, cwd=None):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def fetch(ref: str | None):
    if not (SPEC_DIR / ".git").exists():
        print(f"Cloning {SPEC_REMOTE} into {SPEC_DIR} ...")
        run("git", "clone", SPEC_REMOTE, str(SPEC_DIR))
    else:
        print(f"Fetching latest into existing checkout at {SPEC_DIR} ...")
        run("git", "fetch", "--tags", "--prune", "origin", cwd=SPEC_DIR)

    # This checkout only ever exists to be built from, never edited or
    # committed to -- reset/clean before checkout so a previous
    # interrupted build (or manual poking around) can't leave it in a
    # state a plain checkout would refuse to move off of.
    run("git", "reset", "--hard", cwd=SPEC_DIR)
    run("git", "clean", "-fdx", cwd=SPEC_DIR)

    if ref:
        run("git", "checkout", "--detach", ref, cwd=SPEC_DIR)
    else:
        # Resolves to whichever branch the remote's own HEAD points at
        # (main, master, ...) without hardcoding a name.
        run("git", "remote", "set-head", "origin", "--auto", cwd=SPEC_DIR)
        run("git", "checkout", "--detach", "origin/HEAD", cwd=SPEC_DIR)

    commit = out("git", "rev-parse", "HEAD", cwd=SPEC_DIR)
    commit_short = out("git", "rev-parse", "--short", "HEAD", cwd=SPEC_DIR)
    committed_at = out("git", "log", "-1", "--format=%cI", cwd=SPEC_DIR)
    tag_result = subprocess.run(
        ["git", "describe", "--tags", "--exact-match"],
        cwd=SPEC_DIR, capture_output=True, text=True,
    )
    tag = tag_result.stdout.strip() if tag_result.returncode == 0 else None

    info = {
        "remote": SPEC_REMOTE,
        "ref_requested": ref or "latest",
        "commit": commit,
        "commit_short": commit_short,
        "tag": tag,
        "committed_at": committed_at,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    SPEC_VERSION_FILE.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

    label = tag or commit_short
    print(f"lucid-specification/ is now at {label} (commit {commit_short}, {committed_at})")
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ref",
        help="Tag or commit to check out instead of the latest default-branch commit",
    )
    args = parser.parse_args()
    fetch(args.ref)


if __name__ == "__main__":
    sys.exit(main())
