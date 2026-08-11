from __future__ import annotations
import re
_TREND=("growing quickly","rapid expansion","aggressive expansion","pivotal moment","as you scale","as you grow","point to a period")
_HEDGE=("seems like","must be","will likely","appears to be")
def opener_notes(body:str,ctx:dict)->list[str]:
    first=re.split(r"(?<=[.!?])\s+",(body or "").strip())[0] if body else ""
    low=first.lower(); facts=[(p.get('fact','') if isinstance(p,dict) else str(p)) for p in ctx.get('proof_points',[])]
    figures=[f for f in facts if re.search(r"\d",f)]
    if any(x in low for x in _TREND+_HEDGE) or (not re.search(r"\d",first) and figures):
        if figures:return [f"Make the researched figure the opener, not scenery or a growth trend: {figures[0]!r}."]
        if any(x in low for x in _HEDGE):return ["Remove the guess; use an honest plain opener when research has no figure."]
    return []
