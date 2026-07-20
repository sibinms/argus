"""The whole review in one call: gather context, run every lens in
parallel, curate what they found, then hand the result to whichever
poster the config selects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from dataclasses import replace

from argus.config import Config
from argus.context.gather import Context
from argus.curator.curate import curate
from argus.lenses.base import Finding
from argus.lenses.loader import load_lenses
from argus.models.client import generate_pr_summary, run_lens


def run_review(context: Context, config: Config) -> list[Finding]:
    lenses = load_lenses(config.lenses)

    # Planner: one cheap call before lenses fire. The brief it produces tells
    # every lens what the PR is trying to do and what invariants to verify —
    # exactly the shared context that prevents cross-file bugs from being missed.
    if not context.pr_summary:
        summary = generate_pr_summary(context, config.models.lens)
        context = replace(context, pr_summary=summary)

    all_findings: list[Finding] = []
    with ThreadPoolExecutor(max_workers=max(len(lenses), 1)) as executor:
        futures = [executor.submit(run_lens, lens, context, config.models.lens) for lens in lenses]
        for future in futures:
            all_findings.extend(future.result())

    return curate(all_findings, context, config.models.curator)
