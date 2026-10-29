"""The whole review in one call: gather context, run every lens in
parallel, curate what they found, then hand the result to whichever
poster the config selects."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from argus.config import Config
from argus.context.gather import Context
from argus.checks import Check, load_checks
from argus.curator.curate import curate
from argus.lenses.base import Finding, Lens
from argus.lenses.loader import load_lenses
from argus.models.client import generate_pr_summary, run_lens

logger = logging.getLogger(__name__)


def _run_lens_isolated(
    lens: Lens, context: Context, model: str, fallbacks: Sequence[str] = ()
) -> list[Finding] | None:
    try:
        return run_lens(lens, context, model, fallbacks)
    except Exception:
        # One lens's provider call failing (bad request, timeout, rate limit,
        # anything) shouldn't cost the other seven their findings — that
        # turns a single lens's bad day into the whole review silently
        # vanishing. Log it and skip this lens instead. None (as opposed to
        # an empty list, which means the lens ran fine and genuinely found
        # nothing) marks this as a failure so run_review can tell the two
        # apart — see run_review's docstring on why that distinction matters.
        logger.warning("lens %r failed, skipping it for this review", lens.name, exc_info=True)
        return None


def _run_check_isolated(name: str, check: Check, context: Context) -> list[Finding] | None:
    """Same contract as _run_lens_isolated: [] means the check ran and found
    nothing, None means it blew up (a bug in the check, not a provider
    outage) and was skipped."""
    try:
        return check(context)
    except Exception:
        logger.warning("check %r failed, skipping it for this review", name, exc_info=True)
        return None


def run_review(context: Context, config: Config) -> list[Finding]:
    """Runs the configured deterministic checks and model lenses over the
    context and returns every finding, curated where curation applies.

    Checks go first: they are pure functions over the diff, so there is
    nothing for the curator to verify — their findings come back already
    marked "kept" and are appended after curation untouched. With no lenses
    configured they are the whole review: no planner, no lens, no curator,
    no model call at all.
    """
    lenses = load_lenses(config.lenses)
    checks = load_checks(config.checks)

    check_results = [_run_check_isolated(name, check, context) for name, check in checks]
    check_findings = [f for result in check_results if result is not None for f in result]
    if not lenses:
        if checks and all(result is None for result in check_results):
            raise RuntimeError(
                f"All {len(checks)} check(s) failed — refusing to post a review with no signal"
            )
        return check_findings

    # Planner: one cheap call before lenses fire. The brief it produces tells
    # every lens what the PR is trying to do and what invariants to verify —
    # exactly the shared context that prevents cross-file bugs from being missed.
    if not context.pr_summary:
        summary = generate_pr_summary(context, config.models.lens, config.models.lens_fallbacks)
        context = replace(context, pr_summary=summary)

    with ThreadPoolExecutor(max_workers=max(len(lenses), 1)) as executor:
        futures = [
            executor.submit(
                _run_lens_isolated, lens, context, config.models.lens, config.models.lens_fallbacks
            )
            for lens in lenses
        ]
        results = [future.result() for future in futures]

    # A lens that ran fine and genuinely found nothing returns []; a lens
    # whose call failed returns None (see _run_lens_isolated). Losing some
    # lenses to a bad model call is fine — the rest still provide real
    # signal. Losing ALL of them isn't: an empty findings list is then
    # indistinguishable from "reviewed and found nothing", so proceeding
    # would let Argus post a clean "looks good" verdict on a PR that
    # received zero actual review (see #52 — a total DashScope quota outage
    # did exactly this). Fail loudly instead, the same way a single lens
    # failure used to before isolation was added.
    if lenses and all(result is None for result in results):
        raise RuntimeError(
            f"All {len(lenses)} lens(es) failed — refusing to post a review with no signal"
        )

    all_findings = [finding for result in results if result is not None for finding in result]
    curated = curate(all_findings, context, config.models.curator, config.models.curator_fallbacks)
    return curated + check_findings
