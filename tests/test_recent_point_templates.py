"""A voice may swap a fixed block's text when research reports a recent point of a
given kind -- the "raise-swap" the original HPE voices documented but this app
never implemented.

Before this stage: CustomVoice.recent_point_templates is declared and read by
nothing (grep across app/, engine/ and ui/ finds only the declaration), so a
voice's standing opener always ships regardless of what research found.
"""
import app.compose as compose
from app.models import CustomVoice, Block


def _voice(**kw):
    base = dict(
        id="v", display_name="V",
        blocks=[Block(id="opening", mode="fixed", text="Standing opener about {company}.",
                      length="one_line")],
    )
    base.update(kw)
    return CustomVoice(**base)


def _tokens(detail="a EUR 20m Series B", kind="raise", present=True):
    return {"company": "Acme", "recent": detail if present else "",
            "recent_kind": kind if present else ""}


def test_template_replaces_the_standing_opener_on_a_matching_kind():
    v = _voice(recent_point_templates={"raise": "Congratulations on your recent {recent}."})
    block = v.blocks[0]
    out = compose.resolve_fixed(None, block, _tokens(), [], voice=v)
    assert "Congratulations on your recent a EUR 20m Series B" in out
    assert "Standing opener" not in out


def test_standing_opener_ships_when_the_kind_does_not_match():
    v = _voice(recent_point_templates={"raise": "Congratulations on your recent {recent}."})
    block = v.blocks[0]
    out = compose.resolve_fixed(None, block, _tokens(kind="hire"), [], voice=v)
    assert "Standing opener about Acme" in out


def test_standing_opener_ships_when_no_recent_point_is_present():
    v = _voice(recent_point_templates={"raise": "Congratulations on your recent {recent}."})
    block = v.blocks[0]
    out = compose.resolve_fixed(None, block, _tokens(present=False), [], voice=v)
    assert "Standing opener about Acme" in out


def test_a_voice_with_no_templates_is_completely_unaffected():
    """Every existing voice has an empty map -- none of their behaviour may change."""
    v = _voice()
    block = v.blocks[0]
    out = compose.resolve_fixed(None, block, _tokens(), [], voice=v)
    assert "Standing opener about Acme" in out


def test_templates_are_keyed_by_every_recent_point_kind_not_just_raise():
    """The generic version: any recent_point.kind can carry its own opener."""
    v = _voice(recent_point_templates={"hire": "Saw you brought on {recent}."})
    block = v.blocks[0]
    out = compose.resolve_fixed(None, block, _tokens(detail="a new VP Eng", kind="hire"), [], voice=v)
    assert "Saw you brought on a new VP Eng" in out
