import json
from unittest.mock import MagicMock

import pytest

from argus.context.gather import ChangedFile, Context
from argus.lenses.base import Finding, Lens
from argus.models.client import (
    CURATOR_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    _coerce_line,
    _complete,
    _context_prompt,
    _extract_json,
    _max_input_tokens,
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


def test_complete_defaults_openrouter_to_zero_data_retention(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "[]"
        return resp

    monkeypatch.setattr("argus.models.client.completion", fake_completion)
    _complete("sys", "user", "openrouter/openrouter/free")
    assert captured.get("extra_body") == {"provider": {"zdr": True, "data_collection": "deny"}}


def test_complete_does_not_send_openrouter_params_to_other_providers(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "[]"
        return resp

    monkeypatch.setattr("argus.models.client.completion", fake_completion)
    _complete("sys", "user", "claude-haiku-4-5")
    assert "extra_body" not in captured


def test_pr_summary_appears_in_context_prompt():
    ctx = Context(diff="+x", changed_files=[], pr_summary="## Intent\nFixes a bug.")
    prompt = _context_prompt(ctx, "m", "sys")
    assert "Review brief" in prompt
    assert "Fixes a bug." in prompt


def test_pr_summary_absent_when_empty():
    ctx = Context(diff="+x", changed_files=[], pr_summary="")
    prompt = _context_prompt(ctx, "m", "sys")
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


def test_max_input_tokens_none_for_unknown_model():
    # "m" isn't a real model litellm has metadata for -- no known limit means
    # no trimming, not a crash.
    assert _max_input_tokens("totally-made-up-model-xyz") is None


def test_context_prompt_keeps_everything_when_model_unknown():
    ctx = Context(
        diff="+x",
        changed_files=[ChangedFile(path="a.py", content="print(1)")],
    )
    prompt = _context_prompt(ctx, "totally-made-up-model-xyz", "sys")
    assert "a.py" in prompt


def test_context_prompt_drops_files_from_the_end_when_over_budget(monkeypatch):
    monkeypatch.setattr(
        "argus.models.client.get_model_info", lambda model: {"max_input_tokens": 100}
    )

    # Every prompt containing "b.py" is "over budget"; anything without it
    # fits. This lets us assert the last file is the one that gets dropped,
    # without needing a real tokenizer.
    def fake_token_counter(model, messages):
        return 1000 if "b.py" in messages[1]["content"] else 10

    monkeypatch.setattr("argus.models.client.token_counter", fake_token_counter)

    ctx = Context(
        diff="+x",
        changed_files=[
            ChangedFile(path="a.py", content="print(1)"),
            ChangedFile(path="b.py", content="print(2)"),
        ],
    )
    prompt = _context_prompt(ctx, "m", "sys")
    assert "a.py" in prompt
    assert "b.py" not in prompt


def test_context_prompt_keeps_diff_even_if_still_over_budget(monkeypatch):
    # If dropping every file still isn't enough, there's nothing left to
    # trim -- the diff and PR metadata are never dropped.
    monkeypatch.setattr("argus.models.client.get_model_info", lambda model: {"max_input_tokens": 1})
    monkeypatch.setattr("argus.models.client.token_counter", lambda model, messages: 1000)

    ctx = Context(
        diff="+x",
        changed_files=[ChangedFile(path="a.py", content="print(1)")],
    )
    prompt = _context_prompt(ctx, "m", "sys")
    assert "# Diff" in prompt
    assert "a.py" not in prompt


def test_context_prompt_includes_project_standards():
    ctx = Context(
        diff="+x",
        changed_files=[],
        project_standards="# AGENTS.md\nNo console.log in committed code.",
    )
    prompt = _context_prompt(ctx, "m", "sys")
    assert "# Project standards" in prompt
    assert "No console.log in committed code." in prompt


def test_context_prompt_omits_project_standards_section_when_absent():
    ctx = Context(diff="+x", changed_files=[], project_standards="")
    prompt = _context_prompt(ctx, "m", "sys")
    assert "# Project standards" not in prompt


def test_context_prompt_drops_project_standards_only_after_all_files(monkeypatch):
    # Standards are more broadly useful than any one file's full content, but
    # cheaper to regenerate context from than the diff -- so files must be
    # dropped first, and standards only as a last resort before the diff.
    monkeypatch.setattr(
        "argus.models.client.get_model_info", lambda model: {"max_input_tokens": 100}
    )

    def fake_token_counter(model, messages):
        content = messages[1]["content"]
        if "# Project standards" in content:
            return 1000
        if "a.py" in content:
            return 500
        return 10

    monkeypatch.setattr("argus.models.client.token_counter", fake_token_counter)

    ctx = Context(
        diff="+x",
        changed_files=[ChangedFile(path="a.py", content="print(1)")],
        project_standards="Some binding rule.",
    )
    prompt = _context_prompt(ctx, "m", "sys")
    assert "a.py" not in prompt
    assert "# Project standards" not in prompt
    assert "# Diff" in prompt


def test_context_prompt_keeps_project_standards_when_it_fits_after_files_drop(monkeypatch):
    monkeypatch.setattr(
        "argus.models.client.get_model_info", lambda model: {"max_input_tokens": 100}
    )

    def fake_token_counter(model, messages):
        return 1000 if "a.py" in messages[1]["content"] else 10

    monkeypatch.setattr("argus.models.client.token_counter", fake_token_counter)

    ctx = Context(
        diff="+x",
        changed_files=[ChangedFile(path="a.py", content="print(1)")],
        project_standards="Some binding rule.",
    )
    prompt = _context_prompt(ctx, "m", "sys")
    assert "a.py" not in prompt
    assert "# Project standards" in prompt
    assert "Some binding rule." in prompt


def test_generate_pr_summary_includes_project_standards_in_prompt(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "## Intent\nx"
        return resp

    monkeypatch.setattr("argus.models.client.completion", fake_completion)
    ctx = Context(diff="+x", changed_files=[], project_standards="Binding rule from base.")
    generate_pr_summary(ctx, "model")
    user_prompt = captured["messages"][1]["content"]
    assert "# Project standards" in user_prompt
    assert "Binding rule from base." in user_prompt


def test_generate_pr_summary_omits_project_standards_section_when_absent(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = "## Intent\nx"
        return resp

    monkeypatch.setattr("argus.models.client.completion", fake_completion)
    ctx = Context(diff="+x", changed_files=[], project_standards="")
    generate_pr_summary(ctx, "model")
    assert "# Project standards" not in captured["messages"][1]["content"]


def test_untrusted_input_lines_match_what_each_call_actually_receives():
    # Regression test for a real bug: the planner's untrusted-input line
    # once claimed it receives "file content", but generate_pr_summary()
    # only ever sends pr_title, pr_body, and diff -- never file content,
    # never reply text (see PR #57). Each prompt's line should list exactly
    # what that call sends, not an identical shared sentence.
    assert "file content" not in PLANNER_SYSTEM_PROMPT
    assert "reply text" not in PLANNER_SYSTEM_PROMPT
    assert "PR title" in PLANNER_SYSTEM_PROMPT
    assert "description" in PLANNER_SYSTEM_PROMPT

    lens_prompt = Lens(name="x", instructions="y").system_prompt()
    assert "file content" in lens_prompt
    assert "reply text" not in lens_prompt

    assert "file content" in CURATOR_SYSTEM_PROMPT
    assert "reply text" in CURATOR_SYSTEM_PROMPT
    assert "PR title" in CURATOR_SYSTEM_PROMPT
