"""The 'Congratulations on {det}.' template requires det to be a noun phrase.

A real sent draft opened: "Congratulations on Ahead Health secured EUR 8.7 million
($10 million) in investment in July 2026, led by 3VC and RTP Global, to support its
expansion into Germany and the Netherlands.." The template is only grammatical when
det is a fragment like "your recent Series A"; nothing enforced that shape, so a
full sentence from recent_point.detail was spliced mid-clause into another sentence.
"""
import sys
sys.path.insert(0, "engine")

from engine.draft_engine import _opening_line


def _spec(detail: str) -> dict:
    return {"lead_mode": "news", "opening_fallback": "",
            "recent": {"present": True, "kind": "raise", "detail": detail}}


REAL_BROKEN_DETAIL = (
    "Ahead Health secured EUR 8.7 million ($10 million) in investment in July 2026, "
    "led by 3VC and RTP Global, to support its expansion into Germany and the Netherlands"
)


def test_a_full_sentence_detail_does_not_produce_a_run_on():
    """This is the exact real-world case that shipped."""
    line = _opening_line(_spec(REAL_BROKEN_DETAIL))
    assert not line.startswith("Congratulations on Ahead Health secured"), (
        "the full sentence was spliced directly into the template, producing "
        "'Congratulations on X secured Y..., led by Z, to support...' -- a run-on "
        "with no grammatical subject-verb relationship to the template's own clause"
    )


def test_a_fragment_detail_still_works():
    """The intended, common case must keep working."""
    line = _opening_line(_spec("Ahead Health's recent EUR 8.7m raise"))
    assert line == "Congratulations on Ahead Health's recent EUR 8.7m raise."


def test_a_detail_containing_a_period_is_treated_as_a_sentence():
    line = _opening_line(_spec("Ahead Health closed its round. It plans to expand."))
    assert not line.startswith("Congratulations on Ahead Health closed"), (
        "a detail with an internal period is unambiguously multi-sentence and must "
        "not be treated as a fragment"
    )


def test_an_overlong_detail_is_treated_as_a_sentence_even_without_a_period():
    """A single very long clause with no period is still not a fragment in the sense
    the template needs; length alone is a signal worth checking."""
    long_no_period = "a " * 40 + "raise"
    line = _opening_line(_spec(long_no_period))
    assert "Congratulations on a a a" not in line


def test_a_decimal_number_in_a_fragment_is_not_mistaken_for_a_sentence_boundary():
    """"EUR 8.7m" contains a period that is not a sentence boundary. A naive check
    for "." in det would misclassify this ordinary, short, correct fragment as a
    full sentence and route it into the wrong branch."""
    line = _opening_line(_spec("Ahead Health's recent EUR 8.7m raise"))
    assert line == "Congratulations on Ahead Health's recent EUR 8.7m raise."
