import pytest

from argus.config import Config, ModelConfig
from argus.context.gather import Context
from argus.lenses.base import Finding, Lens
from argus.pipeline import _run_lens_isolated, run_review


def test_run_lens_isolated_returns_none_on_exception(monkeypatch, caplog):
    # None (not []) marks a failure, distinct from a lens that ran fine and
    # genuinely found nothing -- run_review relies on that distinction to
    # tell "some lenses failed" from "every lens failed" (see #52).
    def failing_run_lens(lens, context, model, fallbacks=()):
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
    monkeypatch.setattr(
        "argus.pipeline.run_lens", lambda lens, context, model, fallbacks=(): expected
    )
    lens = Lens(name="security", instructions="look for problems")
    findings = _run_lens_isolated(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings == expected


def test_run_review_survives_one_lens_failing(monkeypatch):
    # Simulate PR #2399: one lens's provider call fails (context too large for
    # the model), the rest should still produce findings and reach curation.
    def fake_run_lens(lens, context, model, fallbacks=()):
        if lens.name == "contracts":
            raise RuntimeError("input length exceeds model limit")
        return [
            Finding(lens=lens.name, file="a.py", line=1, summary="s", detail="", confidence="low")
        ]

    monkeypatch.setattr("argus.pipeline.run_lens", fake_run_lens)
    monkeypatch.setattr(
        "argus.pipeline.generate_pr_summary", lambda context, model, fallbacks=(): ""
    )
    monkeypatch.setattr(
        "argus.pipeline.curate", lambda findings, context, model, fallbacks=(): findings
    )

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
    def always_fails(lens, context, model, fallbacks=()):
        raise RuntimeError("quota exhausted")

    monkeypatch.setattr("argus.pipeline.run_lens", always_fails)
    monkeypatch.setattr(
        "argus.pipeline.generate_pr_summary", lambda context, model, fallbacks=(): ""
    )
    curate_called = False

    def fake_curate(findings, context, model, fallbacks=()):
        nonlocal curate_called
        curate_called = True
        return findings

    monkeypatch.setattr("argus.pipeline.curate", fake_curate)

    config = Config(lenses=["contracts", "security"], models=ModelConfig())
    context = Context(diff="+x", changed_files=[])

    with pytest.raises(RuntimeError, match="All 2 lens"):
        run_review(context, config)

    assert not curate_called


def _check_finding():
    return Finding(
        lens="comments",
        file="a.py",
        line=3,
        summary="long",
        detail="",
        confidence="medium",
        status="kept",
    )


def test_run_review_with_only_checks_never_calls_a_model(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("model call made in a checks-only review")

    monkeypatch.setattr("argus.pipeline.run_lens", boom)
    monkeypatch.setattr("argus.pipeline.generate_pr_summary", boom)
    monkeypatch.setattr("argus.pipeline.curate", boom)
    monkeypatch.setattr(
        "argus.pipeline.load_checks", lambda names: [("comments", lambda ctx: [_check_finding()])]
    )

    config = Config(lenses=[], checks=["comments"])
    findings = run_review(Context(diff="+x", changed_files=[]), config)

    assert [f.lens for f in findings] == ["comments"]
    assert findings[0].status == "kept"


def test_check_findings_skip_the_curator_and_are_appended(monkeypatch):
    lens_finding = Finding(
        lens="security", file="a.py", line=1, summary="s", detail="", confidence="low"
    )
    monkeypatch.setattr(
        "argus.pipeline.run_lens", lambda lens, context, model, fallbacks=(): [lens_finding]
    )
    monkeypatch.setattr(
        "argus.pipeline.generate_pr_summary", lambda context, model, fallbacks=(): ""
    )
    seen_by_curator = []

    def fake_curate(findings, context, model, fallbacks=()):
        seen_by_curator.extend(findings)
        return findings

    monkeypatch.setattr("argus.pipeline.curate", fake_curate)
    monkeypatch.setattr(
        "argus.pipeline.load_checks", lambda names: [("comments", lambda ctx: [_check_finding()])]
    )

    config = Config(lenses=["security"], checks=["comments"])
    findings = run_review(Context(diff="+x", changed_files=[]), config)

    assert [f.lens for f in seen_by_curator] == ["security"]
    assert [f.lens for f in findings] == ["security", "comments"]


def test_run_review_survives_a_check_crashing(monkeypatch, caplog):
    def broken(ctx):
        raise RuntimeError("bug in check")

    monkeypatch.setattr("argus.pipeline.load_checks", lambda names: [("comments", broken)])
    monkeypatch.setattr(
        "argus.pipeline.run_lens",
        lambda lens, context, model, fallbacks=(): [
            Finding(lens=lens.name, file="a.py", line=1, summary="s", detail="", confidence="low")
        ],
    )
    monkeypatch.setattr(
        "argus.pipeline.generate_pr_summary", lambda context, model, fallbacks=(): ""
    )
    monkeypatch.setattr(
        "argus.pipeline.curate", lambda findings, context, model, fallbacks=(): findings
    )

    config = Config(lenses=["security"], checks=["comments"])
    with caplog.at_level("WARNING"):
        findings = run_review(Context(diff="+x", changed_files=[]), config)

    assert [f.lens for f in findings] == ["security"]
    assert "comments" in caplog.text


def test_run_review_raises_when_checks_only_and_every_check_fails(monkeypatch):
    def broken(ctx):
        raise RuntimeError("bug in check")

    monkeypatch.setattr("argus.pipeline.load_checks", lambda names: [("comments", broken)])
    config = Config(lenses=[], checks=["comments"])
    with pytest.raises(RuntimeError, match="All 1 check"):
        run_review(Context(diff="+x", changed_files=[]), config)
