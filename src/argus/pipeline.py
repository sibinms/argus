"""The whole review in one call: gather context, run every lens in
parallel, curate what they found, then hand the result to whichever
poster the config selects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from argus.config import Config
from argus.context.gather import Context
from argus.curator.curate import curate
from argus.lenses.base import Finding
from argus.lenses.loader import load_lenses
from argus.models.client import run_lens


def run_review(context: Context, config: Config) -> list[Finding]:
    lenses = load_lenses(config.lenses)

    all_findings: list[Finding] = []
    with ThreadPoolExecutor(max_workers=max(len(lenses), 1)) as executor:
        futures = [executor.submit(run_lens, lens, context, config.models.lens) for lens in lenses]
        for future in futures:
            all_findings.extend(future.result())

    return curate(all_findings, context, config.models.curator)
