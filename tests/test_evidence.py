from argus.context.gather import ChangedFile, Context
from argus.curator.evidence import quote_appears_in_context


def _context():
    return Context(
        diff="+    query = f\"SELECT * FROM merchants WHERE name = '{name}'\"",
        changed_files=[ChangedFile(path="a.py", content="def foo():\n    return None")],
    )


def test_quote_found_in_diff():
    assert quote_appears_in_context("SELECT * FROM merchants", _context())


def test_quote_found_in_file_content_with_whitespace_differences():
    assert quote_appears_in_context("def   foo():", _context())


def test_quote_not_found():
    assert not quote_appears_in_context("this text does not exist anywhere", _context())


def test_empty_or_short_quote_rejected():
    assert not quote_appears_in_context(None, _context())
    assert not quote_appears_in_context("  ", _context())
    assert not quote_appears_in_context("hi", _context())
