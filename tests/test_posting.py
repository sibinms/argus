from argus.posting.github import _patch_new_lines


def test_added_and_context_lines_are_commentable():
    patch = "@@ -10,3 +10,4 @@ def f():\n context_a\n+added_b\n context_c\n-removed_d\n"
    # new-file numbering starts at 10: context_a=10, added_b=11, context_c=12
    assert _patch_new_lines(patch) == {10, 11, 12}


def test_removed_lines_do_not_advance_new_file_counter():
    patch = "@@ -5,4 +5,2 @@\n-gone_1\n-gone_2\n kept_5\n+new_6\n"
    # removed lines contribute no new-file line; kept_5=5, new_6=6
    assert _patch_new_lines(patch) == {5, 6}


def test_multiple_hunks():
    patch = "@@ -1,1 +1,1 @@\n+first\n@@ -20,1 +30,2 @@\n ctx\n+second\n"
    assert _patch_new_lines(patch) == {1, 30, 31}


def test_empty_or_none_patch():
    assert _patch_new_lines(None) == set()
    assert _patch_new_lines("") == set()


def test_file_header_lines_ignored():
    patch = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n a\n+b\n"
    assert _patch_new_lines(patch) == {1, 2}
