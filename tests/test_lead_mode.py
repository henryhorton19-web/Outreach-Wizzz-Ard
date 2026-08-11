import engine.draft_engine as de


def _spec(**kw):
    base = {"greeting": "Hi Antoine,", "ask": "Open to a quick chat?", "opening_fallback": "",
            "allow_dashes": False, "link_strength": "none", "allowed_facts": [], "allowed_numbers": [],
            "recent": {"present": True, "detail": "your Series C", "kind": "raise"}}
    base.update(kw)
    return base


def test_default_behaviour_is_unchanged():
    assert "congratulations" in de.finalize(_spec(), {"body": "A body."})["email"].lower()


def test_noticing_suppresses_news_openers_and_keeps_body():
    spec = _spec(lead_mode="noticing")
    out = de.finalize(spec, {"body": "A body about their product."})["email"]
    assert "congratulations" not in out.lower()
    assert "A body about their product." in out
    assert out.strip().endswith("Open to a quick chat?")
