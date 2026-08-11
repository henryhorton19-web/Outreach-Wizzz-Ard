"""Advisory checks that make separately selectable profile evidence visible."""
from __future__ import annotations

_BUILD = ("built", "shipped", "wrote", "automated", "engineered", "coded", "designed")
_ANALYSIS = ("diligenced", "analysed", "analyzed", "modelled", "modeled", "valued", "researched", "evaluated", "screened")
_CRAFT = ("lines of code", "loc", "tests", "test suite", "static gate", "static analysis", "ci pipeline", "coverage", "linter", "type hints", "refactor")
_SELF_HARM = ("bug", "broken", "500", "crash", "failure", "was returning")


def split_suggestions(experiences: dict) -> list[str]:
    out = []
    for key, exp in (experiences or {}).items():
        anchor = str((exp or {}).get("anchor") or "").lower()
        if any(word in anchor for word in _BUILD) and any(word in anchor for word in _ANALYSIS):
            out.append(f"'{key}' covers analysis and building; split it so each is selectable on its own merits.")
    return out


def craft_notes(experiences: dict) -> list[str]:
    out = []
    for key, exp in (experiences or {}).items():
        for fact in ((exp or {}).get("facts") or []):
            low = str(fact).lower()
            if any(signal in low for signal in _CRAFT):
                out.append(f"'{key}': describe scope and outcome rather than implementation craft.")
            elif any(signal in low for signal in _SELF_HARM):
                out.append(f"'{key}': do not lead with a defect in your own work.")
    return out
