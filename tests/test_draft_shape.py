import app.draft_shape as ds


def test_self_assessment_and_menu_get_actionable_notes():
    body = "This blend could be useful for M&A, fundraising, or automation."
    notes = ds.shape_notes(body, {"proof_points": [], "observation": ""})
    assert any("would do" in note.lower() or "one" in note.lower() for note in notes)


def test_researched_specific_passes():
    ctx = {"proof_points": [{"fact": "500 certified instructors across 560 cities"}], "observation": ""}
    body = "With 500 instructors across 560 cities, the first thing I would look at is onboarding."
    assert ds.shape_notes(body, ctx) == []
