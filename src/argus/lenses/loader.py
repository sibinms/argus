from __future__ import annotations

from pathlib import Path

from argus.lenses.base import Lens

BUILTIN_DIR = Path(__file__).parent / "builtin"


def _load_builtin(name: str) -> Lens:
    path = BUILTIN_DIR / f"{name}.md"
    if not path.exists():
        raise ValueError(f"Unknown builtin lens: {name}")
    return Lens(name=name, instructions=path.read_text())


def _load_custom(path_str: str) -> Lens:
    path = Path(path_str)
    if not path.exists():
        raise ValueError(f"Custom lens file not found: {path_str}")
    return Lens(name=path.stem, instructions=path.read_text())


def load_lenses(lens_config: list) -> list[Lens]:
    """lens_config entries are either a builtin name (str) or a mapping
    {"custom": "path/to/lens.md"}, matching .argus/config.yml's `lenses` list."""
    lenses = []
    for entry in lens_config:
        if isinstance(entry, str):
            lenses.append(_load_builtin(entry))
        elif isinstance(entry, dict) and "custom" in entry:
            lenses.append(_load_custom(entry["custom"]))
        else:
            raise ValueError(f"Invalid lens config entry: {entry!r}")
    return lenses
