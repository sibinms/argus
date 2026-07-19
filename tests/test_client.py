import json
from unittest.mock import MagicMock

import pytest

from argus.context.gather import Context
from argus.lenses.base import Finding
from argus.models.client import _complete, _extract_json, curate_with_model


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
    _complete("sys", "user", "m", 100)
    assert captured.get("timeout")


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
