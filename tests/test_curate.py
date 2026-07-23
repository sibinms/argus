import sys

from argus.context.gather import ChangedFile, Context
from argus.curator.curate import curate, recurate_with_replies
from argus.fingerprint import fingerprint
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


def test_recurate_with_replies_ignores_findings_with_no_reply(monkeypatch):
    def boom(findings, ctx, model):
        raise AssertionError("curate_with_model should not be called with no replies")

    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(module, "curate_with_model", boom)

    f = _finding()
    out = recurate_with_replies([f], {}, _ctx(), "m")
    assert out == [f]
    assert f.detail == "d"  # untouched


def test_recurate_with_replies_folds_reply_into_detail_and_reapplies_decision(monkeypatch):
    f = _finding()
    fp = fingerprint(f)
    captured = {}

    def fake_curate(findings, ctx, model):
        captured["detail"] = findings[0].detail
        return [{"action": "drop_noise", "reason": "author explained it's intentional"}]

    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(module, "curate_with_model", fake_curate)

    out = recurate_with_replies([f], {fp: ["this is intentional, see ENG-123"]}, _ctx(), "m")

    # the curator saw the reply...
    assert "this is intentional, see ENG-123" in captured["detail"]
    assert out[0].status == "dropped"
    assert out[0].drop_reason == "author explained it's intentional"
    # ...but the finding's own detail — rendered verbatim in the posted
    # comment when kept/downgraded — is never mutated with it.
    assert out[0].detail == "d"
    assert "ENG-123" not in out[0].detail


def test_recurate_with_replies_degrades_gracefully_on_curator_failure(monkeypatch, caplog):
    # A transient failure re-judging the reply shouldn't abort the whole
    # run — the original finding (unmodified) should still come back.
    f = _finding()
    fp = fingerprint(f)

    def boom(findings, ctx, model):
        raise RuntimeError("curator API down")

    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(module, "curate_with_model", boom)

    with caplog.at_level("WARNING"):
        out = recurate_with_replies([f], {fp: ["a reply"]}, _ctx(), "m")

    assert out[0] is f
    assert out[0].status == "proposed"  # untouched, not re-judged
    assert "failed to re-curate findings with replies" in caplog.text


def test_recurate_with_replies_still_requires_a_real_quote_for_drop(monkeypatch):
    # A reply can't unilaterally prove a finding wrong the way a diff quote
    # can — "drop" via a reply with no real evidence_quote must still refuse
    # and downgrade instead, same rule as a normal curation pass.
    f = _finding()
    fp = fingerprint(f)

    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(
        module,
        "curate_with_model",
        lambda findings, ctx, model: [
            {"action": "drop", "reason": "trust me", "evidence_quote": "nowhere in the diff"}
        ],
    )

    out = recurate_with_replies([f], {fp: ["trust me, it's fine"]}, _ctx(), "m")
    assert out[0].status == "downgraded"
    assert out[0].confidence == "low"


def test_recurate_with_replies_leaves_unmatched_findings_untouched(monkeypatch):
    f1 = _finding()
    f2 = Finding(
        lens="tests", file="b.py", line=1, summary="other issue", detail="d2", confidence="medium"
    )
    fp1 = fingerprint(f1)

    module = sys.modules["argus.curator.curate"]
    monkeypatch.setattr(
        module,
        "curate_with_model",
        lambda findings, ctx, model: [{"action": "drop_noise", "reason": "r"}],
    )

    out = recurate_with_replies([f1, f2], {fp1: ["a reply"]}, _ctx(), "m")
    assert out[0].status == "dropped"  # re-judged
    assert out[1].detail == "d2"  # f2 has no reply, left exactly as-is
    assert out[1].status == "proposed"
