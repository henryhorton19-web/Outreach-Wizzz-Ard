"""Golden set quality evaluation for the link matcher.

Asserts:
1. Overall agreement with hand-labelled golden set >= 80%.
2. CRITICAL SAFETY INVARIANT: Never returns 'strong' on a case labelled 'none'.
"""
import json
from pathlib import Path
import pytest

import engine.config as EC
import app.link_matcher as lm


def _load_cases():
    p = Path("tests/fixtures/link_cases.json")
    return json.loads(p.read_text(encoding="utf-8"))


def test_link_quality_golden_set():
    cases = _load_cases()
    assert len(cases) >= 12, "Golden set must have at least 12 cases"
    
    exps = [dict(v, _key=k) for k, v in EC.CANDIDATE_PROFILE.get("experiences", {}).items()]
    
    matches = 0
    false_strong_on_none = 0
    
    for case in cases:
        cache = case["cache"]
        expected = case["expected_strength"]
        res = lm.resolve_link(cache, exps)
        actual = res["link_strength"]
        
        if expected == "none" and actual == "strong":
            false_strong_on_none += 1
            
        if actual == expected:
            matches += 1
            
    assert false_strong_on_none == 0, f"Safety violation: {false_strong_on_none} cases labelled 'none' received 'strong'"
    accuracy = matches / len(cases)
    assert accuracy >= 0.80, f"Accuracy {accuracy:.1%} below 80% threshold ({matches}/{len(cases)})"
