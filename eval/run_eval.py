"""Replays a small set of known bugs through the full pipeline and reports
recall: how many of them a fresh run actually catches.

This is the metric that matters. The project this tool is modelled on found
that its dashboards looked fine for months while the reviewer silently
never generated the findings that mattered — "zero findings" and "correct"
looked identical from the outside. Recall against a seed set of real,
already-known bugs is the only thing that catches that.

Usage: ANTHROPIC_API_KEY=... python eval/run_eval.py

Add your own seeds under eval/seed_bugs/<name>/ with a diff.patch and an
expected.yml (see the two examples already there) — ideally pulled from
your own repo's git history, not invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from argus.config import Config  # noqa: E402
from argus.context.gather import Context  # noqa: E402
from argus.pipeline import run_review  # noqa: E402

SEED_DIR = Path(__file__).parent / "seed_bugs"


def _keyword_overlap(hint: str, text: str) -> float:
    hint_words = {w.lower() for w in hint.split() if len(w) > 3}
    text_words = {w.lower() for w in text.split() if len(w) > 3}
    if not hint_words:
        return 0.0
    return len(hint_words & text_words) / len(hint_words)


def run_seed(seed_dir: Path, config: Config) -> tuple[int, int, list[str]]:
    expected = yaml.safe_load((seed_dir / "expected.yml").read_text())
    diff_text = (seed_dir / "diff.patch").read_text()

    context = Context(
        diff=diff_text,
        changed_files=[],
        pr_title=expected.get("title", ""),
        pr_body=expected.get("description", ""),
    )

    findings = run_review(context, config)
    postable = [f for f in findings if f.status in ("kept", "downgraded")]

    caught = 0
    misses = []
    for bug in expected["bugs"]:
        hit = any(
            bug.get("file") in (f.file or "")
            and _keyword_overlap(bug["hint"], f"{f.summary} {f.detail}") > 0.3
            for f in postable
        )
        if hit:
            caught += 1
        else:
            misses.append(bug["id"])

    return caught, len(expected["bugs"]), misses


def main():
    config = Config()  # defaults: all built-in lenses, shadow mode
    total_caught = 0
    total_bugs = 0

    for seed_dir in sorted(SEED_DIR.iterdir()):
        if not seed_dir.is_dir():
            continue
        caught, total, misses = run_seed(seed_dir, config)
        total_caught += caught
        total_bugs += total
        status = "OK" if not misses else f"MISSED: {', '.join(misses)}"
        print(f"{seed_dir.name}: {caught}/{total} — {status}")

    recall = total_caught / total_bugs if total_bugs else 0.0
    print(f"\nOverall recall: {total_caught}/{total_bugs} ({recall:.0%})")


if __name__ == "__main__":
    main()
