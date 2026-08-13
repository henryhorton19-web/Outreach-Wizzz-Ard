"""Variation pressure for the intent-first voice (Plan 30).

The problem this solves: ten letters off one skeleton reused a single credential six times and a
single link sentence verbatim six times. The guidance asked for the right things and the model
complied with the SHAPE of the examples given, then swapped the nouns. An instruction to "vary" does
not help, because the model has no way of knowing what it has already written.

So variation is derived from state instead. Plan 26 already stores every approved letter per voice in
the exemplar corpus. This module reads that history, works out which credential and which link move
each letter used, and produces a directive naming what has been leaned on recently. Fit still wins:
the directive asks for a different move only where the fit is comparable, because a badly matched
credential is worse than a repeated one.

Detection is deterministic substring matching on signature phrases, not a model call. That keeps this
cheap enough to run on every draft and makes it testable offline.

App layer only. No I/O of its own -- callers pass the letters in. Never raises.
"""
from __future__ import annotations

import re

# Each move is a distinct grammatical shape for the sentence that links the sender's finding to the
# company. The old voice mandated exactly one of these, which is why six letters ended identically.
LINK_MOVES = {
    "at_proper_scale":
        "\"<company> is that same problem at proper scale.\" Plainest option. Use when the company is "
        "doing what you did, larger.",
    "other_side":
        "\"<company> is that same problem from the other side.\" Use when you were the consumer of what "
        "they produce, or vice versa.",
    "buyer_side":
        "\"That is your thesis from the buyer's side.\" Use when your experience is evidence FOR their "
        "argument that they cannot get from their own customers.",
    "solved_properly":
        "\"<company> is solving that properly; mine was <crude version>.\" Use when you built a rough "
        "version of their product. Name your crude version, do not flatter theirs.",
    "named_component":
        "\"<product name> is that same idea applied to <X>.\" Use only when the company has a named "
        "product worth knowing. Naming it shows you read past the homepage.",
    "bare":
        "No link sentence at all. Write the finding so the connection is obvious and stop. Use when any "
        "link sentence would be spelling out what the reader has already understood.",
}

# Signature phrases, longest-first so a specific move wins over a general one.
_MOVE_SIGNATURES = (
    ("buyer_side", ("from the buyer's side", "from the buyer side")),
    ("solved_properly", ("solving that properly", "solving the same problem properly")),
    ("other_side", ("from the other side",)),
    ("at_proper_scale", ("at proper scale",)),
    ("named_component", ("applied to",)),
)

# Credential key -> signature phrases. These must stay in step with evidence.custom_facts on the
# voice: a credential the corpus cannot detect is a credential the directive cannot balance.
_CREDENTIAL_SIGNATURES = (
    ("sourcing_system", ("system for pe funds", "sourcing tool", "find companies, research them",
                         "find and research companies")),
    ("outreach_engine", ("my own outreach engine",)),
    ("behavioural_economics", ("behavioural economics",)),
    ("econometrics", ("studied econometrics",)),
    ("growth_equity", ("summer in growth equity", "growth equity doing commercial diligence",
                       "commercial diligence")),
    ("tutoring_business", ("tutoring business",)),
    ("hpe_internationalisation", ("internationalisation",)),
)

_RECENT = 5          # how far back to look; beyond this the operator's own style has moved on
_OVERUSED = 2        # a move or credential used this many times in the window is "leaned on"

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset((
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "is", "are", "was", "were",
    "with", "by", "as", "that", "this", "it", "its", "their", "they", "we", "our", "you", "your",
    "i", "my", "me", "had", "have", "has", "be", "been", "but", "not", "no", "up", "out", "so",
    "what", "which", "when", "how", "than", "then", "there", "here", "into", "about", "from",
))


def _terms(text) -> set:
    try:
        out = set()
        for w in _WORD_RE.findall(str(text or "").lower()):
            if w not in _STOP and len(w) > 3:
                out.add(w)
                if len(w) > 4 and w.endswith("s"):
                    out.add(w[:-1])
        return out
    except Exception:
        return set()


def detect_move(letter) -> str:
    """Which link move a letter used, or "bare" when none is recognisable. Never raises."""
    try:
        low = str(letter or "").lower()
        if not low.strip():
            return "bare"
        for key, sigs in _MOVE_SIGNATURES:
            if any(s in low for s in sigs):
                return key
        return "bare"
    except Exception:
        return "bare"


def detect_credential(letter) -> str:
    """Which credential a letter cited, or "" when none is recognisable. Never raises."""
    try:
        low = str(letter or "").lower()
        if not low.strip():
            return ""
        for key, sigs in _CREDENTIAL_SIGNATURES:
            if any(s in low for s in sigs):
                return key
        return ""
    except Exception:
        return ""


def variation_directive(letters) -> str:
    """A prompt fragment naming what recent letters leaned on, and what to reach for instead.

    Returns "" when there is no history, so a first letter is composed exactly as before.
    Fit outranks novelty: this asks for a different move only where the match is comparable, because
    a mismatched credential reads as a non-sequitur and a repeated one merely reads as consistent.
    Never raises.
    """
    try:
        recent = [x for x in (letters or []) if isinstance(x, str) and x.strip()][-_RECENT:]
        if not recent:
            return ""
        moves, creds = {}, {}
        for l in recent:
            m = detect_move(l)
            moves[m] = moves.get(m, 0) + 1
            c = detect_credential(l)
            if c:
                creds[c] = creds.get(c, 0) + 1
        used_moves = sorted(moves, key=lambda k: -moves[k])
        unused = [k for k in LINK_MOVES if k not in moves]
        over_creds = sorted([c for c, n in creds.items() if n >= _OVERUSED],
                            key=lambda c: -creds[c])

        lines = ["\n--- VARIATION (what the last letters already used) ---"]
        lines.append("Link moves already used: " + ", ".join(used_moves) + ".")
        if unused:
            lines.append("Not yet used, prefer one of these where the fit is as good: "
                         + ", ".join(unused) + ".")
        else:
            lines.append("Every move has been used; pick the best fit and vary the wording within it.")
        if over_creds:
            lines.append("Credentials already leaned on: " + ", ".join(over_creds) +
                         ". Choose a different one unless it is clearly the only one that explains "
                         "this company's problem. A mismatched credential is worse than a repeated one.")
        lines.append("Do not reuse a finding you have already written. If the honest finding for this "
                     "company is one you have used, say it in a different sentence shape.")
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def relevance_notes(finding, features, *, min_overlap: int = 1) -> list[str]:
    """Flag a finding that shares no content with what the company actually does.

    Catches the observed GetMint failure: a GEO company received a finding about the sender's own
    research pipeline being low quality. The two are unrelated, and nothing checked. Deliberately
    lenient -- one shared content term passes -- because the aim is to catch a non-sequitur, not to
    police word choice. Never raises.
    """
    try:
        f = _terms(finding)
        if not f:
            return []
        ctx = _terms((features or {}).get("what_they_do", "")) | \
              _terms((features or {}).get("situation_read", ""))
        if not ctx:
            return []
        if len(f & ctx) >= max(1, int(min_overlap)):
            return []
        return ["The finding in the second sentence shares nothing with what this company does. "
                "Rewrite it so it is about the problem they work on, or choose a different credential."]
    except Exception:
        return []
