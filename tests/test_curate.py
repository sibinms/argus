import sys

from argus.context.gather import ChangedFile, Context
from argus.curator.curate import curate
from argus.lenses.base import Finding


def _ctx():
    return Context(
        diff="+    x = compute_total()\n",
        changed_files=[ChangedFile(path="a.py", content="x = compute_total()")],
    )


def _finding():
    return Finding(
        lens="security",
        file="a.py",
        line=1,
        summary="user input reaches the query unescaped",
        detail="d",
        confidence="medium",
    )


def _patch_decisions(monkeypatch, decisions):
    # The argus.curator package re-exports the `curate` function, which shadows
    # the submodule of the same name, so fetch the real module from sys.modules.
    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(module, "curate_with_model", lambda findings, ctx, model: decisions)


def test_drop_noise_drops_without_a_quote(monkeypatch):
    _patch_decisions(
        monkeypatch,
        [{"action": "drop_noise", "reason": "only describes the change", "evidence_quote": None}],
    )
    out = curate([_finding()], _ctx(), "m")
    assert out[0].status == "dropped"
    assert out[0].drop_reason == "only describes the change"


def test_factual_drop_kept_when_quote_is_real(monkeypatch):
    _patch_decisions(
        monkeypatch,
        [{"action": "drop", "reason": "already handled", "evidence_quote": "x = compute_total()"}],
    )
    out = curate([_finding()], _ctx(), "m")
    assert out[0].status == "dropped"


def test_factual_drop_refused_when_quote_absent(monkeypatch):
    _patch_decisions(
        monkeypatch,
        [{"action": "drop", "reason": "nah", "evidence_quote": "this text is nowhere in the diff"}],
    )
    out = curate([_finding()], _ctx(), "m")
    # can't verify the quote -> refuse the drop, keep it downgraded
    assert out[0].status == "downgraded"
    assert out[0].confidence == "low"


def test_keep_sets_confidence(monkeypatch):
    _patch_decisions(
        monkeypatch,
        [{"action": "keep", "confidence": "high", "reason": "real", "evidence_quote": None}],
    )
    out = curate([_finding()], _ctx(), "m")
    assert out[0].status == "kept"
    assert out[0].confidence == "high"
