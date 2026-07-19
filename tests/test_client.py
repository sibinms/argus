import json

import pytest

from argus.models.client import _extract_json


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
