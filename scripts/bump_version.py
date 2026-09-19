#!/usr/bin/env python3
"""Bump Memdex's version in every place that records it.

Policy (see AGENTS.md): **every change ships a patch bump.** Minor and major
bumps are deliberate product decisions — the maintainer asks for those, so this
script refuses them unless you pass --confirm, which is the whole point of
running the bump through a script instead of six hand edits.

    python scripts/bump_version.py            # 1.0.0-beta -> 1.0.1-beta
    python scripts/bump_version.py minor --confirm
    python scripts/bump_version.py major --confirm
    python scripts/bump_version.py --set 1.2.3-beta --confirm
    python scripts/bump_version.py --show

Two spellings are written from one source of truth: the display version
(1.0.1-beta) and the PEP 440 form pyproject needs (1.0.1b0). A unit test asserts
they stay the same release.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "memdex" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
DOCKERFILE = ROOT / "Dockerfile"
README = ROOT / "README.md"
CHANGELOG = ROOT / "changelog.html"

RELEASE_ANCHOR = '  <section class="release">'
TAGS = ("add", "fix", "note")

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?$")


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: str = ""

    @classmethod
    def parse(cls, text: str) -> Version:
        match = VERSION_RE.match(text.strip().lstrip("v"))
        if not match:
            raise SystemExit(
                f"Not a version this project uses: {text!r} (want 1.2.3 or 1.2.3-beta)"
            )
        major, minor, patch, pre = match.groups()
        return cls(int(major), int(minor), int(patch), pre or "")

    @property
    def display(self) -> str:
        """What `memdex --version` prints."""
        return f"{self.major}.{self.minor}.{self.patch}" + (f"-{self.pre}" if self.pre else "")

    @property
    def pep440(self) -> str:
        """What packaging tools require."""
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.pre:
            return base
        marker = {"alpha": "a", "beta": "b", "rc": "rc"}.get(self.pre.split(".")[0])
        if marker is None:
            raise SystemExit(f"Unsupported pre-release label {self.pre!r} (use alpha, beta or rc)")
        return f"{base}{marker}0"

    def bump(self, part: str) -> Version:
        if part == "patch":
            return Version(self.major, self.minor, self.patch + 1, self.pre)
        if part == "minor":
            return Version(self.major, self.minor + 1, 0, self.pre)
        if part == "major":
            return Version(self.major + 1, 0, 0, self.pre)
        raise SystemExit(f"Unknown part {part!r}")


def current() -> Version:
    match = re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"No __version__ found in {INIT}")
    return Version.parse(match.group(1))


def _swap(path: Path, old: str, new: str, *, required: bool = True) -> bool:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        if required:
            raise SystemExit(f"Could not find {old!r} in {path} — update it by hand.")
        return False
    path.write_text(text.replace(old, new), encoding="utf-8")
    return True


def apply(old: Version, new: Version) -> list[str]:
    touched = []
    _swap(INIT, f'__version__ = "{old.display}"', f'__version__ = "{new.display}"')
    touched.append(str(INIT.relative_to(ROOT)))

    _swap(PYPROJECT, f'version = "{old.pep440}"', f'version = "{new.pep440}"')
    touched.append(str(PYPROJECT.relative_to(ROOT)))

    if _swap(
        DOCKERFILE,
        f'org.opencontainers.image.version="{old.display}"',
        f'org.opencontainers.image.version="{new.display}"',
        required=False,
    ):
        touched.append(str(DOCKERFILE.relative_to(ROOT)))

    if _swap(README, f"`v{old.display}`", f"`v{new.display}`", required=False):
        touched.append(str(README.relative_to(ROOT)))
    _swap(
        README,
        f"**v{old.display}**\n(package metadata: `{old.pep440}`)",
        f"**v{new.display}**\n(package metadata: `{new.pep440}`)",
        required=False,
    )
    return touched


def changelog_entry(tag: str, what: str, why: str) -> str:
    why_html = f'\n          <span class="why">{html.escape(why)}</span>' if why else ""
    return (
        "        <li>\n"
        f'          <span class="tag {tag}">{tag}</span>'
        f'<span class="what">{html.escape(what)}</span>{why_html}\n'
        "        </li>\n"
    )


def record_change(version: Version, tag: str, what: str, why: str) -> None:
    """Add the entry to changelog.html, creating the release section if needed.

    Several changes can land on one version: the second and later ones are
    appended to that version's existing list rather than starting a new section.
    """
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = changelog_entry(tag, what, why)
    heading = f"<h2>{version.display}</h2>"

    if heading in text:
        start = text.index(heading)
        marker = text.index("      <ul>\n", start) + len("      <ul>\n")
        text = text[:marker] + entry + text[marker:]
    else:
        section = (
            f'  <section class="release">\n'
            f'    <div class="release-head">\n'
            f"      {heading}\n"
            f'      <span class="pill">patch</span>\n'
            f'      <span class="date">{date.today().strftime("%-d %B %Y")}</span>\n'
            f"    </div>\n\n"
            f'    <div class="group">\n'
            f"      <h3>Changes</h3>\n"
            f"      <ul>\n{entry}      </ul>\n"
            f"    </div>\n"
            f"  </section>\n\n"
        )
        if RELEASE_ANCHOR not in text:
            raise SystemExit(f"Could not find a release section in {CHANGELOG}")
        at = text.index(RELEASE_ANCHOR)
        text = text[:at] + section + text[at:]

    CHANGELOG.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "part", nargs="?", default="patch", choices=["patch", "minor", "major"],
        help="Which component to bump (default: patch).",
    )
    parser.add_argument("--set", dest="exact", help="Set an exact version, e.g. 1.2.3-beta.")
    parser.add_argument(
        "--confirm", action="store_true",
        help="Required for minor, major or --set: those are the maintainer's call.",
    )
    parser.add_argument("--show", action="store_true", help="Print the current version and exit.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Say what would change, write nothing."
    )
    parser.add_argument("--note", help="What changed — one line for changelog.html.")
    parser.add_argument("--why", default="", help="Why it changed, for the changelog entry.")
    parser.add_argument(
        "--tag", default="fix", choices=list(TAGS), help="Entry kind (default: fix)."
    )
    parser.add_argument(
        "--no-changelog", action="store_true",
        help="Skip the changelog entry. Only for a bump that changes no behavior.",
    )
    args = parser.parse_args()

    now = current()
    if args.show:
        print(f"{now.display}  (pep440: {now.pep440})")
        return 0

    if args.exact:
        target = Version.parse(args.exact)
        needs_confirm = True
    else:
        target = now.bump(args.part)
        needs_confirm = args.part != "patch"

    if needs_confirm and not args.confirm:
        what = "an exact version" if args.exact else f"a {args.part} bump"
        print(
            f"Refusing {what} without --confirm.\n\n"
            "Memdex bumps the patch version on every change; minor and major releases are\n"
            "the maintainer's decision. Re-run with --confirm if that decision was made.",
            file=sys.stderr,
        )
        return 1

    # Every version carries its own changelog line — that is the policy, so the
    # tool asks for it rather than trusting anyone to remember.
    if not args.note and not args.no_changelog:
        print(
            "Refusing to bump without --note.\n\n"
            "Every version records what changed, e.g.\n"
            '  python scripts/bump_version.py --tag fix \\\n'
            '      --note "Dotted paths keep their dot" \\\n'
            '      --why "\'.memdex/config.yaml\' was read as a path that exists nowhere."\n\n'
            "Use --no-changelog only for a bump that changes no behavior.",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print(f"{now.display} -> {target.display}  (pep440: {now.pep440} -> {target.pep440})")
        if args.note:
            print(f'changelog: [{args.tag}] {args.note}')
        return 0

    touched = apply(now, target)
    if args.note:
        record_change(target, args.tag, args.note, args.why)
        touched.append(str(CHANGELOG.relative_to(ROOT)))
    print(f"✓ {now.display} -> {target.display}  (pep440: {target.pep440})")
    for path in touched:
        print(f"    {path}")
    if args.no_changelog:
        print("\n! No changelog entry was written (--no-changelog).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
