import pytest

from argus.config import Config, ModelConfig
from argus.context.gather import Context
from argus.lenses.base import Finding, Lens
from argus.pipeline import _run_lens_isolated, run_review


def test_run_lens_isolated_returns_none_on_exception(monkeypatch, caplog):
    # None (not []) marks a failure, distinct from a lens that ran fine and
    # genuinely found nothing -- run_review relies on that distinction to
    # tell "some lenses failed" from "every lens failed" (see #52).
    def failing_run_lens(lens, context, model):
        raise RuntimeError("boom")

    monkeypatch.setattr("argus.pipeline.run_lens", failing_run_lens)
    lens = Lens(name="security", instructions="look for problems")
    with caplog.at_level("WARNING"):
        result = _run_lens_isolated(lens, Context(diff="+x", changed_files=[]), "m")
    assert result is None
    assert "security" in caplog.text


def test_run_lens_isolated_passes_through_on_success(monkeypatch):
    expected = [
        Finding(lens="security", file="a.py", line=1, summary="s", detail="", confidence="low")
    ]
    monkeypatch.setattr("argus.pipeline.run_lens", lambda lens, context, model: expected)
    lens = Lens(name="security", instructions="look for problems")
    findings = _run_lens_isolated(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings == expected


def test_run_review_survives_one_lens_failing(monkeypatch):
    # Simulate PR #2399: one lens's provider call fails (context too large for
    # the model), the rest should still produce findings and reach curation.
    def fake_run_lens(lens, context, model):
        if lens.name == "contracts":
            raise RuntimeError("input length exceeds model limit")
        return [
            Finding(lens=lens.name, file="a.py", line=1, summary="s", detail="", confidence="low")
        ]

    monkeypatch.setattr("argus.pipeline.run_lens", fake_run_lens)
    monkeypatch.setattr("argus.pipeline.generate_pr_summary", lambda context, model: "")
    monkeypatch.setattr("argus.pipeline.curate", lambda findings, context, model: findings)

    config = Config(lenses=["contracts", "security"], models=ModelConfig())
    context = Context(diff="+x", changed_files=[])

    findings = run_review(context, config)

    assert len(findings) == 1
    assert findings[0].lens == "security"


def test_run_review_raises_when_all_lenses_fail(monkeypatch):
    # Simulate nomoding/nomod-api#2409: a total provider outage (DashScope
    # free-tier quota exhausted) fails every lens. An empty findings list is
    # then indistinguishable from "reviewed and found nothing", so proceeding
    # would let Argus post a clean "looks good" verdict on a PR that received
    # zero actual review. run_review must fail loudly instead of curating an
    # empty list.
    def always_fails(lens, context, model):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr("argus.pipeline.run_lens", always_fails)
    monkeypatch.setattr("argus.pipeline.generate_pr_summary", lambda context, model: "")
    curate_called = False

    def fake_curate(findings, context, model):
        nonlocal curate_called
        curate_called = True
        return findings

    monkeypatch.setattr("argus.pipeline.curate", fake_curate)

    config = Config(lenses=["contracts", "security"], models=ModelConfig())
    context = Context(diff="+x", changed_files=[])

    with pytest.raises(RuntimeError, match="All 2 lens"):
        run_review(context, config)

    assert not curate_called
