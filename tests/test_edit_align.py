"""Offline tests for edit alignment & classification (Plan 26, Stage 3).

An edit to a machine draft can be:
  - slot (word/phrase swap within a block, keeping surrounding text);
  - structural (block moved, split, combined, or replaced entirely);
  - register (overall tone/style shift across multiple blocks).

Only slot edits preserve the template skeleton; structural and register edits tear it down and
force induction to rebuild blocks around the user's new phrasing.
"""
import pytest

from app import edit_align


def test_identical_text_has_zero_diff():
    m = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    b = {"greeting": "Hi Ana,", "body": "I saw the uptime page.", "close": "Happy to help."}
    res = edit_align.classify(machine_email=m, machine_blocks=b, final_email=m)
    assert res["kind"] == "unchanged"
    assert res["slot_edits"] == {}
    assert res["overall_effort"] == 0.0


def test_single_word_swap_is_slot_edit():
    m = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    f = "Hi Ana,\n\nI noticed the uptime page.\n\nHappy to help."
    b = {"greeting": "Hi Ana,", "body": "I saw the uptime page.", "close": "Happy to help."}
    res = edit_align.classify(machine_email=m, machine_blocks=b, final_email=f)
    assert res["kind"] == "slot"
    assert "body" in res["slot_edits"]
    assert res["slot_edits"]["body"]["original"] == "I saw the uptime page."
    assert res["slot_edits"]["body"]["edited"] == "I noticed the uptime page."


def test_greeting_swap_is_slot_edit_on_frame_block():
    m = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    f = "Ana,\n\nI saw the uptime page.\n\nHappy to help."
    b = {"greeting": "Hi Ana,", "body": "I saw the uptime page.", "close": "Happy to help."}
    res = edit_align.classify(machine_email=m, machine_blocks=b, final_email=f)
    assert res["kind"] == "slot"
    assert "greeting" in res["slot_edits"]


def test_heavy_rewrite_is_classified_as_structural():
    m = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    f = "Hey Ana -- scaling payments is tough. We helped Stripe cut latency by 30ms. Free Tuesday?"
    b = {"greeting": "Hi Ana,", "body": "I saw the uptime page.", "close": "Happy to help."}
    res = edit_align.classify(machine_email=m, machine_blocks=b, final_email=f)
    assert res["kind"] in ("structural", "register")
    assert res["overall_effort"] > 0.5


def test_missing_machine_blocks_degrades_gracefully():
    m = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    f = "Hi Ana,\n\nI saw the uptime page.\n\nBest, Ana."
    res = edit_align.classify(machine_email=m, machine_blocks={}, final_email=f)
    assert res["kind"] in ("slot", "structural", "register")


def test_span_alignment_finds_unchanged_surrounding_text():
    orig = "I saw your series A announcement yesterday on TechCrunch."
    edit = "I saw your series B announcement yesterday on TechCrunch."
    spans = edit_align.align_block(orig, edit)
    assert len(spans) == 3
    assert spans[0] == ("fixed", "I saw your series ")
    assert spans[1] == ("edited", "A", "B")
    assert spans[2] == ("fixed", " announcement yesterday on TechCrunch.")


def test_completely_different_block_has_no_fixed_spans():
    orig = "Hi Ana,"
    edit = "To whom it may concern:"
    spans = edit_align.align_block(orig, edit)
    assert len(spans) == 1
    assert spans[0][0] == "edited"


def test_blank_box_authored_email_is_unclassified():
    res = edit_align.classify(machine_email="", machine_blocks={},
                             final_email="Hi Ana,\n\nTyped by hand.")
    assert res["kind"] == "authored"
    assert res["overall_effort"] == 1.0
