import json
from unittest.mock import MagicMock

import pytest

from argus.context.gather import Context
from argus.lenses.base import Finding, Lens
from argus.models.client import (
    _coerce_line,
    _complete,
    _context_prompt,
    _extract_json,
    curate_with_model,
    generate_pr_summary,
    run_lens,
)


def test_extracts_plain_json_array():
    assert _extract_json('[{"summary": "x"}]') == [{"summary": "x"}]


def test_extracts_fenced_json():
    assert _extract_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_salvages_array_wrapped_in_prose():
    # A model that prepends chatter shouldn't cost us the whole lens's output.
    assert _extract_json('Here are the findings: [{"a": 1}]. Hope that helps!') == [{"a": 1}]


def test_raises_when_no_array_present():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("no json here at all")


def _fake_completion(content):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return lambda **kwargs: resp


def test_curator_keeps_everything_when_output_is_unparseable(monkeypatch):
    # If the curator model returns junk, we must not silently drop findings —
    # fall back to keeping them all rather than losing real issues.
    monkeypatch.setattr("argus.models.client.completion", _fake_completion("not json at all"))
    findings = [
        Finding(lens="x", file="a.py", line=1, summary="s", detail="d", confidence="medium")
    ]
    decisions = curate_with_model(findings, Context(diff="+x", changed_files=[]), "m")
    assert len(decisions) == 1
    assert decisions[0]["action"] == "keep"


def test_complete_sets_a_request_timeout(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "[]"
        return resp

    monkeypatch.setattr("argus.models.client.completion", fake_completion)
    _complete("sys", "user", "m")
    assert captured.get("timeout")


def test_pr_summary_appears_in_context_prompt():
    ctx = Context(diff="+x", changed_files=[], pr_summary="## Intent\nFixes a bug.")
    prompt = _context_prompt(ctx)
    assert "Review brief" in prompt
    assert "Fixes a bug." in prompt


def test_pr_summary_absent_when_empty():
    ctx = Context(diff="+x", changed_files=[], pr_summary="")
    prompt = _context_prompt(ctx)
    assert "Review brief" not in prompt


def test_generate_pr_summary_returns_model_output(monkeypatch):
    monkeypatch.setattr(
        "argus.models.client.completion", _fake_completion("## Intent\nAdds a feature.")
    )
    ctx = Context(diff="+x", changed_files=[], pr_title="feat: add thing")
    result = generate_pr_summary(ctx, "model")
    assert "Adds a feature" in result


def test_generate_pr_summary_returns_empty_on_error(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr("argus.models.client.completion", boom)
    ctx = Context(diff="+x", changed_files=[])
    assert generate_pr_summary(ctx, "model") == ""


def test_generate_pr_summary_preserves_the_documented_brief_sections(monkeypatch):
    # PLANNER_SYSTEM_PROMPT asks the model for three named sections. This
    # doesn't validate the model's compliance (that's on the model), but it
    # does prove generate_pr_summary passes a compliant brief through intact
    # rather than truncating or reformatting it.
    brief = (
        "## Intent\nAdds a feature.\n\n"
        "## Key invariants\n- thing stays true\n\n"
        "## What to verify\n- does X happen?"
    )
    monkeypatch.setattr("argus.models.client.completion", _fake_completion(brief))
    ctx = Context(diff="+x", changed_files=[])
    result = generate_pr_summary(ctx, "model")
    assert "## Intent" in result
    assert "## Key invariants" in result
    assert "## What to verify" in result


def test_generate_pr_summary_logs_warning_on_error(monkeypatch, caplog):
    def boom(**kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr("argus.models.client.completion", boom)
    ctx = Context(diff="+x", changed_files=[])
    with caplog.at_level("WARNING"):
        generate_pr_summary(ctx, "model")
    assert "planner failed" in caplog.text


def test_run_lens_logs_warning_on_unparseable_output(monkeypatch, caplog):
    monkeypatch.setattr("argus.models.client.completion", _fake_completion("not json at all"))
    lens = Lens(name="security", instructions="look for problems")
    with caplog.at_level("WARNING"):
        findings = run_lens(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings == []
    assert "security" in caplog.text


def test_coerce_line_accepts_int():
    assert _coerce_line(42) == 42


def test_coerce_line_accepts_none():
    assert _coerce_line(None) is None


def test_coerce_line_rejects_bool():
    # bool is a subclass of int in Python; a lens has no business reporting
    # True/False as a line number, so treat it as absent rather than 1/0.
    assert _coerce_line(True) is None
    assert _coerce_line(False) is None


def test_coerce_line_rejects_float():
    assert _coerce_line(4.2) is None


def test_coerce_line_rejects_list():
    assert _coerce_line([42]) is None


def test_run_lens_coerces_string_line_numbers_to_int(monkeypatch):
    # Some models return "line" as a numeric string rather than an int. If it
    # isn't coerced, Finding.line ends up a str, which later blows up the
    # curator's dedupe distance check (int - str).
    monkeypatch.setattr(
        "argus.models.client.completion",
        _fake_completion('[{"summary": "s", "line": "42"}]'),
    )
    lens = Lens(name="x", instructions="look for problems")
    findings = run_lens(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings[0].line == 42
    assert isinstance(findings[0].line, int)


def test_run_lens_drops_unparseable_line_to_none(monkeypatch):
    monkeypatch.setattr(
        "argus.models.client.completion",
        _fake_completion('[{"summary": "s", "line": "not-a-number"}]'),
    )
    lens = Lens(name="x", instructions="look for problems")
    findings = run_lens(lens, Context(diff="+x", changed_files=[]), "m")
    assert findings[0].line is None


def test_curator_keeps_everything_on_count_mismatch(monkeypatch):
    # Curator returned fewer decisions than findings -> keep all, don't zip-drop.
    monkeypatch.setattr("argus.models.client.completion", _fake_completion("[]"))
    findings = [
        Finding(lens="x", file="a.py", line=1, summary="one", detail="", confidence="low"),
        Finding(lens="y", file="b.py", line=2, summary="two", detail="", confidence="low"),
    ]
    decisions = curate_with_model(findings, Context(diff="+x", changed_files=[]), "m")
    assert len(decisions) == 2
    assert all(d["action"] == "keep" for d in decisions)
