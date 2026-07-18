from argus.curator.curate import dedupe
from argus.lenses.base import Finding


def _finding(**overrides):
    base = dict(
        lens="security",
        file="app.py",
        line=10,
        summary="user input reaches the query unescaped",
        detail="",
        confidence="medium",
    )
    base.update(overrides)
    return Finding(**base)


def test_dedupe_merges_similar_findings_from_different_lenses():
    findings = [
        _finding(lens="security", confidence="medium"),
        _finding(lens="contracts", confidence="high", line=11),
    ]
    merged = dedupe(findings)
    assert len(merged) == 1
    assert merged[0].confidence == "high"
    assert "security" in merged[0].lens and "contracts" in merged[0].lens


def test_dedupe_keeps_findings_in_different_files_separate():
    findings = [
        _finding(file="app.py"),
        _finding(file="other.py"),
    ]
    merged = dedupe(findings)
    assert len(merged) == 2


def test_dedupe_keeps_unrelated_findings_in_same_file_separate():
    findings = [
        _finding(summary="user input reaches the query unescaped"),
        _finding(summary="missing test for the new discount branch", line=200),
    ]
    merged = dedupe(findings)
    assert len(merged) == 2
