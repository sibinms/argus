from argus.config import Config, ModelConfig
from argus.context.gather import Context
from argus.lenses.base import Finding, Lens
from argus.pipeline import _run_lens_isolated, run_review


def test_run_lens_isolated_returns_empty_on_exception(monkeypatch, caplog):
    def failing_run_lens(lens, context, model):
        raise RuntimeError("boom")

    monkeypatch.setattr("argus.pipeline.run_lens", failing_run_lens)
    lens = Lens(name="security", instructions="look for problems")
    with caplog.at_level("WARNING"):
        findings = _run_lens_isolated(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings == []
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
