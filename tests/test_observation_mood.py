import json
import pathlib

from app.observation_sampler import select_observation


def test_schema_allows_tension():
    schema = json.loads((pathlib.Path(__file__).parent.parent / "engine/schema.json").read_text())
    assert "tension" in schema["properties"]["earned_observation"]["properties"]["mood"]["enum"]


def test_tension_outranks_hedged_hypothesis():
    tension = {"read": "five minute integration and a full banking licence at once", "mood": "tension", "p": .1}
    hypothesis = {"read": "a key operational challenge will likely be scaling", "mood": "hypothesis", "p": .9}
    assert select_observation([tension, hypothesis]) is tension
