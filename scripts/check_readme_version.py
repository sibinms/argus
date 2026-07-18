"""Fails if the version(s) pinned in README.md don't match pyproject.toml.

This is exactly the drift that bit this project twice already: bump one,
forget the other, and the copy-paste Action example quietly points at a
stale tag. Run in CI on every push/PR so it's caught immediately instead
of by eye at release time.

Usage: python scripts/check_readme_version.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> int:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    expected = "v" + pyproject["project"]["version"]

    readme = (ROOT / "README.md").read_text()
    found = set(re.findall(r"v\d+\.\d+\.\d+", readme))

    if not found:
        print("No version references found in README.md — nothing to check.")
        return 0

    stale = found - {expected}
    if stale:
        print(
            f"README references {sorted(stale)} but pyproject.toml says "
            f"{expected}.\nUpdate the README's version references (or bump "
            f"pyproject.toml) so they match."
        )
        return 1

    print(f"README version references match pyproject.toml ({expected}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
