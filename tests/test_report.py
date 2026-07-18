from argus.config import PostingConfig
from argus.lenses.base import Finding
from argus.report import postable_findings, verdict


def _finding(**overrides):
    base = dict(
        lens="security",
        file="app.py",
        line=10,
        summary="s",
        detail="d",
        confidence="medium",
        status="kept",
    )
    base.update(overrides)
    return Finding(**base)


def test_postable_findings_excludes_dropped():
    findings = [_finding(status="dropped"), _finding(status="kept")]
    posting = PostingConfig(min_confidence="low")
    result = postable_findings(findings, posting)
    assert len(result) == 1
    assert result[0].status == "kept"


def test_postable_findings_respects_confidence_threshold():
    findings = [_finding(confidence="low"), _finding(confidence="high")]
    posting = PostingConfig(min_confidence="medium")
    result = postable_findings(findings, posting)
    assert len(result) == 1
    assert result[0].confidence == "high"


def test_verdict_approve_when_nothing_postable():
    posting = PostingConfig(min_confidence="medium")
    assert verdict([_finding(status="dropped")], posting) == "approve"


def test_verdict_request_changes_on_high_confidence():
    posting = PostingConfig(min_confidence="low")
    assert verdict([_finding(confidence="high")], posting) == "request_changes"


def test_verdict_comment_on_medium_confidence_only():
    posting = PostingConfig(min_confidence="low")
    assert verdict([_finding(confidence="medium")], posting) == "comment"
