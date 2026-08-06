"""Assembly + the edit path.

MACHINE DRAFT (once): de.finalize(spec, parts) assembles greeting + opening + body + ask (with
the Sciences Po line), normalises stray dashes on the machine text, and runs critique. We store
the assembled email and the normalised composed body (the anchor the edit path finds and replaces).

REVIEWER EDIT PATH (source of truth): the reviewer's body is stored VERBATIM and the final email is
re-assembled by SUBSTITUTING ONLY THE BODY REGION of the machine draft, never by reconstructing the
frame. The human's text is NOT re-normalised and NOT re-validated: what they wrote ships as written.
"""
from __future__ import annotations

from .engine_bridge import de


def machine_draft(spec: dict, parts: dict) -> dict:
    """Return {email, report, machine_body}. `parts` is the composed {body, ask}."""
    result = de.finalize(spec, parts)
    machine_body = de.normalize((parts.get("body") or "").strip(),
                                keep_dashes=bool(spec.get("allow_dashes", False)))
    return {"email": result["email"], "report": result["report"], "machine_body": machine_body}


def reassemble_with_edit(machine_email: str, machine_body: str, edited_body: str) -> str:
    edited_body = edited_body if edited_body is not None else ""
    if machine_body and machine_body in machine_email:
        return machine_email.replace(machine_body, edited_body, 1)
    if not machine_body:
        return edited_body
    return machine_email.replace(machine_body, edited_body) or edited_body


def frame_regions(machine_email: str, machine_body: str) -> dict:
    idx = machine_email.find(machine_body) if machine_body else -1
    if idx == -1:
        return {"prefix": "", "body": machine_email, "suffix": ""}
    return {"prefix": machine_email[:idx], "body": machine_body,
            "suffix": machine_email[idx + len(machine_body):]}


def assemble_custom(spec: dict, body: str, vdef) -> str:
    """Assemble a custom-voice machine draft: greeting, opening, body, positioning/boilerplate,
    close, signoff. Frame blocks are dash-normalised (they are machine-generated); `body` is passed
    already normalised so it matches the stored machine_body anchor exactly (the edit path swaps the
    body region verbatim via reassemble_with_edit, which is unchanged)."""
    kd = bool(getattr(vdef, "allow_dashes", False)) if vdef else bool(spec.get("allow_dashes", False))
    blocks = [
        de.normalize((spec.get("greeting", "") or "").strip(), keep_dashes=kd),
        de.normalize((spec.get("opening", "") or "").strip(), keep_dashes=kd),
        (body or "").strip(),
        de.normalize((spec.get("boilerplate", "") or "").strip(), keep_dashes=kd),
        de.normalize((spec.get("close", "") or "").strip(), keep_dashes=kd),
        de.normalize((spec.get("signoff", "") or "").strip(), keep_dashes=kd),
    ]
    return "\n\n".join(b for b in blocks if b)
