from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
VERSION_PY = ROOT / "trend_analyzer" / "version.py"
README_MD = ROOT / "README.md"
DEV_LOG_MD = ROOT / "DEVELOPMENT_LOG_RU.md"
CHANGELOG_MD = ROOT / "CHANGELOG.md"


SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
APP_VERSION_RE = re.compile(r'^(APP_VERSION\s*=\s*")(\d+\.\d+\.\d+)(")\s*$', re.MULTILINE)
README_TITLE_RE = re.compile(r"^#\s*Trend Analyzer v\d+\.\d+\.\d+\s*$", re.MULTILINE)
DEVLOG_TITLE_RE = re.compile(r"(`Trend Analyzer v)\d+\.\d+\.\d+(`)")


@dataclass
class SemVer:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        m = SEMVER_RE.match(value.strip())
        if not m:
            raise ValueError(f"Invalid version format: {value!r}")
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    def bump(self, part: str) -> "SemVer":
        if part == "major":
            return SemVer(self.major + 1, 0, 0)
        if part == "minor":
            return SemVer(self.major, self.minor + 1, 0)
        if part == "patch":
            return SemVer(self.major, self.minor, self.patch + 1)
        raise ValueError(f"Unknown bump part: {part}")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def read_current_version() -> SemVer:
    text = VERSION_PY.read_text(encoding="utf-8")
    m = APP_VERSION_RE.search(text)
    if not m:
        raise RuntimeError("Could not find APP_VERSION in trend_analyzer/version.py")
    return SemVer.parse(m.group(2))


def write_version(new_version: SemVer) -> None:
    text = VERSION_PY.read_text(encoding="utf-8")
    replaced = APP_VERSION_RE.sub(rf'\g<1>{new_version}\g<3>', text, count=1)
    if replaced == text:
        raise RuntimeError("Could not update APP_VERSION in trend_analyzer/version.py")
    VERSION_PY.write_text(replaced, encoding="utf-8")


def update_readme_title(new_version: SemVer) -> None:
    if not README_MD.exists():
        return
    text = README_MD.read_text(encoding="utf-8")
    repl = f"# Trend Analyzer v{new_version}"
    if README_TITLE_RE.search(text):
        text = README_TITLE_RE.sub(repl, text, count=1)
    else:
        text = repl + "\n\n" + text
    README_MD.write_text(text, encoding="utf-8")


def update_devlog_title(new_version: SemVer) -> None:
    if not DEV_LOG_MD.exists():
        return
    text = DEV_LOG_MD.read_text(encoding="utf-8")
    text = DEVLOG_TITLE_RE.sub(rf"\g<1>{new_version}\g<2>", text, count=1)
    DEV_LOG_MD.write_text(text, encoding="utf-8")


def ensure_changelog_exists() -> None:
    if CHANGELOG_MD.exists():
        return
    CHANGELOG_MD.write_text(
        "# Changelog\nВсе значимые изменения проекта фиксируются в этом файле.\n\n",
        encoding="utf-8",
    )


def prepend_changelog_entry(new_version: SemVer, note: str) -> None:
    ensure_changelog_exists()
    text = CHANGELOG_MD.read_text(encoding="utf-8")
    version_tag = f"## [{new_version}] - {date.today().isoformat()}"
    entry = (
        f"{version_tag}\n"
        "### Changed\n"
        f"- {note.strip()}\n\n"
    )

    # Do not duplicate if exact version heading already exists.
    if re.search(rf"^## \[{re.escape(str(new_version))}\] - ", text, flags=re.MULTILINE):
        text = re.sub(
            rf"(^## \[{re.escape(str(new_version))}\] - .*$)",
            r"\1\n### Changed\n- " + note.strip(),
            text,
            count=1,
            flags=re.MULTILINE,
        )
        CHANGELOG_MD.write_text(text, encoding="utf-8")
        return

    if text.startswith("# "):
        parts = text.split("\n", 2)
        if len(parts) >= 3:
            new_text = parts[0] + "\n" + parts[1] + "\n\n" + entry + parts[2].lstrip("\n")
        else:
            new_text = text.rstrip() + "\n\n" + entry
    else:
        new_text = "# Changelog\n\n" + entry + text
    CHANGELOG_MD.write_text(new_text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bump SemVer and append an entry to CHANGELOG.md",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--part", choices=["patch", "minor", "major"], help="SemVer bump type")
    group.add_argument("--set", dest="set_version", help="Set explicit version, e.g. 1.2.0")
    parser.add_argument(
        "--note",
        required=True,
        help="Changelog note for the new version",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = read_current_version()

    if args.set_version:
        target = SemVer.parse(args.set_version)
    else:
        target = current.bump(args.part)

    if str(target) == str(current):
        print(f"Version unchanged: {current}")
        return 0

    write_version(target)
    update_readme_title(target)
    update_devlog_title(target)
    prepend_changelog_entry(target, args.note)

    print(f"Version updated: {current} -> {target}")
    print(f"Changelog entry added to {CHANGELOG_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
