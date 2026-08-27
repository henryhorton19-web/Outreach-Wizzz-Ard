"""Detectors return revision guidance, not verdicts.

The earlier design had these return booleans consumed by critique(), which runs
after assembly and whose result nothing acts on. A scalar reject cannot improve a
draft; a sentence describing what to change can.
"""
import app.draft_feedback as df

exsaas = ("Having judged companies from the investor side and also built sourcing pipelines and "
        "automation for private markets myself, exsaas really stood out to me.")


def _ctx(**kw):
    base = {"style_examples": [exsaas], "observation": "", "proof_points": [], "situation_read": ""}
    base.update(kw)
    return base


def test_copied_scaffold_produces_an_instruction():
    body = ("Having judged companies from the investor side at Example Capital and also built an end to "
            "end LLM pipeline at Meridian AI, Webyn really stood out to me.")
    notes = df.feedback_for(body, _ctx())
    assert notes, "no feedback produced for a copied scaffold"
    joined = " ".join(notes).lower()
    assert "rewrite" in joined or "different" in joined, \
        f"feedback is a verdict, not an instruction: {notes}"


def test_an_original_body_produces_no_feedback():
    body = ("Keeping a no-technical-friction promise gets harder with every agent you add, and that "
            "is what made me look twice.")
    assert df.feedback_for(body, _ctx()) == []


def test_unused_observation_is_named_in_the_feedback():
    """The instruction must say WHAT to use, not merely that something is missing."""
    ctx = _ctx(observation="holding a no-technical-friction promise as agents multiply")
    notes = df.feedback_for("You are likely focused on expanding your reach and impact.", ctx)
    assert any("no-technical-friction" in n for n in notes), \
        f"feedback did not name the specific unused material: {notes}"


def test_sender_heavy_feedback_states_the_correction():
    body = ("Having judged companies at Example Capital and built an LLM pipeline at Meridian AI, my "
            "background combining diligence with building could be useful. I am curious.")
    notes = df.feedback_for(body, _ctx())
    assert any("you" in n.lower() for n in notes)


def test_feedback_is_capped():
    """A revision prompt with ten complaints produces worse output, not better."""
    ctx = _ctx(observation="a specific tension", proof_points=["a specific number"])
    notes = df.feedback_for("Having judged companies from the investor side and also built things.", ctx)
    assert len(notes) <= 3, f"too many simultaneous instructions: {len(notes)}"
