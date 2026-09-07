"""Deterministic checks: pure functions over the gathered Context that emit
Findings with no model call at all.

A lens is a prompt on a cheap model that is told to over-report and then
gets second-guessed by the curator. A check is the opposite end of the
spectrum: a rule precise enough to state in code (count these lines, match
this pattern), so it runs identically on every PR, costs nothing, needs no
API key, and its findings skip the curator -- there is nothing for a model
to verify about "these six consecutive added lines are all comments".

Configured via the `checks:` list in .argus/config.yml, separately from
`lenses:`, so a repo can run any mix -- including checks alone with
`lenses: []` for a review that never touches a model.
"""

from __future__ import annotations

from collections.abc import Callable

from argus.checks.comments import check_long_inline_comments
from argus.context.gather import Context
from argus.lenses.base import Finding

Check = Callable[[Context], list[Finding]]

CHECK_REGISTRY: dict[str, Check] = {
    "comments": check_long_inline_comments,
}


def load_checks(check_config: list) -> list[tuple[str, Check]]:
    """check_config entries are builtin check names (str), matching
    .argus/config.yml's `checks` list."""
    checks = []
    for entry in check_config:
        if not isinstance(entry, str) or entry not in CHECK_REGISTRY:
            raise ValueError(f"Unknown builtin check: {entry!r}")
        checks.append((entry, CHECK_REGISTRY[entry]))
    return checks
