"""The honesty-floor guard suite. numeric_guard is the highest-value port -- it is
the guard responsible for rejecting any figure not present in the researched facts,
and critique() currently has no equivalent at all: an invented number in a drafted
body is caught by nothing but a human reviewer.
"""
import engine.draft_engine as de


def test_numeric_guard_flags_an_invented_figure():
    facts = [{"fact": "Raised a $12m Series A", "source": "https://x.com"}]
    body = "I saw you raised $40m, congratulations."
    hits = de.numeric_guard(body, facts, allowed_numbers=set())
    assert hits, "an invented figure ($40m, not in the facts) was not flagged"


def test_numeric_guard_allows_a_figure_present_in_the_facts():
    facts = [{"fact": "Raised a $12m Series A", "source": "https://x.com"}]
    body = "Congratulations on the $12m Series A."
    hits = de.numeric_guard(body, facts, allowed_numbers=set())
    assert not hits, f"a figure present in the facts was wrongly flagged: {hits}"


def test_unauthorized_commitments_guard_exists_and_flags():
    body = "I can guarantee we'll close this within two weeks."
    hits = de.find_unauthorized_commitments(body)
    assert hits


def test_unearned_superlatives_guard_exists_and_flags():
    body = "We are unmatched in this space and always deliver the best results."
    hits = de.find_unearned_superlatives(body)
    assert hits


def test_dramatised_opener_guard_exists_and_flags():
    body = "It's not about the product. It's about the people behind it."
    hits = de.find_dramatised_opener(body)
    assert hits


def test_org_only_guards_do_not_fire_for_a_self_voice_with_no_precedent_block():
    """The organisation-only guards (unallowed precedent, fund/identity-mechanics
    leak) must not fire against a self-audience voice that has no precedent or
    identity-record block to check in the first place -- confirmed by construction,
    not by an audience flag alone, since a self voice structurally has nothing for
    these guards to look at."""
    spec = {"audience": "self", "precedent_ids": [], "identity_paragraph": ""}
    assert de.find_unallowed_precedent("", spec) == []
    assert de.find_identity_mechanics_leak("", spec) == []
