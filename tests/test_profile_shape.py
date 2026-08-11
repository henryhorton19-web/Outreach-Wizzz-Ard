import app.profile_shape as ps


def test_mixed_row_is_flagged():
    exp = {"hpe": {"anchor": "I diligenced software and shipped an outreach system."}}
    assert ps.split_suggestions(exp)


def test_scope_facts_pass_and_craft_facts_do_not():
    assert not ps.craft_notes({"x": {"facts": ["It finds companies and writes outreach."]}})
    assert ps.craft_notes({"x": {"facts": ["Around 22,000 lines with 354 tests."]}})
