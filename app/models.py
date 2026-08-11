"""Pydantic state models for a batch. JSON is canonical; these describe its shape.

Per-target state machine (no screening/gating; nothing rejects a target except a hard
research disqualifier, which lands it in `error` with a note):
    queued  -> (user clicks Draft →) -> input -> researched -> drafted -> in_review
            -> edited -> approved -> verifying -> ready
'queued' is a pre-pipeline holding state: the target is stored as name+ref only, the engine
never touches it. 'error' is a terminal-ish state; the row stays visible and reviewable.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Literal
from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator


class State(str, Enum):
    queued = "queued"       # lightweight queue — name+ref only, engine never runs
    input = "input"
    researched = "researched"
    drafted = "drafted"
    in_review = "in_review"
    edited = "edited"
    approved = "approved"
    verifying = "verifying"
    ready = "ready"
    error = "error"


# States that occupy one of the 15 active draft slots
DRAFT_SLOT_STATES = frozenset({
    State.input, State.researched, State.drafted,
    State.in_review, State.edited, State.approved,
    State.verifying, State.error,
})


FACT_SCOPES = ("recent", "target_proofs", "situation_read", "profile_evidence",
               "profile_spine", "custom_facts", "earned_observation")
BLOCK_LENGTHS = ("one_line", "short", "medium", "body")
BLOCK_MODES = ("fixed", "ai")


class Block(BaseModel):
    """One unit of the email. Fixed blocks ship their text verbatim after token substitution; AI
    blocks are written by the model from guidance, scoped to the facts they request. There is no
    special 'body' block type: the narrative is just an AI block with length='body' and a wide
    fact_scope. This one abstraction subsumes the old fixed frame + separately-composed body."""
    model_config = {"protected_namespaces": ()}

    id: str
    label: str = ""
    mode: str = "fixed"                        # fixed | ai
    text: str = ""                             # fixed content, or optional AI seed; supports tokens
    guidance: str = ""                         # AI instructions (mode=ai)
    fact_scope: list[str] = Field(default_factory=list)   # subset of FACT_SCOPES; [] = no facts
    length: str = "short"                      # one_line | short | medium | body
    optional: bool = False                     # skip when its scoped facts are absent (e.g. no recent)


class Style(BaseModel):
    """Structured style that compiles deterministically into prompt directives, plus freeform notes
    and gold examples. Replaces the single free-text register so style has reliable effect."""
    model_config = {"populate_by_name": True, "protected_namespaces": ()}

    formality: int = 2                         # 0 very casual .. 4 formal
    warmth: int = 2                            # 0 cool .. 4 warm
    directness: int = 3                        # 0 diplomatic .. 4 blunt
    sentence_length: str = "flowing"           # short | medium | flowing
    hedging: str = "neutral"                   # hedged | neutral | assertive
    humor: str = "none"                        # none | dry | light
    person_focus: str = "recipient_first"      # recipient_first | sender_first | balanced
    proof_density: str = "single"              # single | few | several
    notes: str = Field(default="", alias="register")   # freeform (the old register lives here)
    examples: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """How a voice steers WHICH of the candidate's experiences are selected, and how much is said.
    prefer/pin/exclude and category_weights tilt the deterministic bridge scoring; count sets how
    many are tied; custom_facts widen the allowed-fact set with voice-specific true claims."""
    model_config = {"protected_namespaces": ()}

    prefer: list[str] = Field(default_factory=list)
    pin: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    category_weights: dict[str, int] = Field(default_factory=dict)
    count: int = 2
    custom_facts: list[str] = Field(default_factory=list)
    identity_note: str = ""


def _canonical_blocks_from_legacy(d: dict) -> list[dict]:
    """Convert the old fixed-frame voice shape (greeting/opening/boilerplate/close/signoff with
    per-block modes) into the new ordered block list. Used by the migration validator."""
    def mode(m):
        return "ai" if m == "llm" else "fixed"
    blocks = []
    blocks.append({"id": "greeting", "label": "Greeting", "mode": "fixed",
                   "text": d.get("greeting", "")})
    use_recent = d.get("opening_use_recent", True)
    blocks.append({"id": "opening", "label": "Opening", "mode": mode(d.get("opening_mode", "fixed")),
                   "text": d.get("opening", ""), "guidance": d.get("opening_guidance", ""),
                   "fact_scope": (["recent"] if use_recent else []), "length": "one_line",
                   "optional": bool(use_recent)})
    blocks.append({"id": "body", "label": "Body", "mode": "ai",
                   "guidance": d.get("body_guidance", ""),
                   "fact_scope": ["target_proofs", "profile_evidence", "profile_spine",
                                  "situation_read"], "length": "body"})
    blocks.append({"id": "positioning", "label": "Positioning",
                   "mode": mode(d.get("boilerplate_mode", "fixed")),
                   "text": d.get("boilerplate", ""), "guidance": d.get("boilerplate_guidance", ""),
                   "fact_scope": (["profile_spine"] if d.get("boilerplate_mode") == "llm" else []),
                   "length": "short"})
    blocks.append({"id": "close", "label": "Close", "mode": mode(d.get("close_mode", "fixed")),
                   "text": d.get("close", ""), "guidance": d.get("close_guidance", ""),
                   "length": "one_line"})
    if (d.get("signoff") or "").strip():
        blocks.append({"id": "signoff", "label": "Sign-off", "mode": "fixed", "text": d["signoff"]})
    return blocks


class CustomVoice(BaseModel):
    """A voice: the primary authority over how an email is produced. Pure editable data — the three
    that ship are seeded once into the store and are otherwise identical to any the user creates.

    A voice owns an ordered list of `blocks` (structure + per-block fixed/AI mode), a structured
    `style`, `evidence` preferences that steer which candidate experiences are selected, its own
    word-count `length`, custom `variables`, and floor opt-ins (`allow_dashes`).
    Everything above the honesty floor is the voice's to set. Old-schema JSON is migrated on load.
    """
    model_config = {"populate_by_name": True, "protected_namespaces": ()}

    id: str
    display_name: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    seeded_from: str = "blank"
    kind: str = "outreach"                                # "outreach" | "followup" — which set this belongs to

    situations: list[str] = Field(default_factory=list)   # auto-routing tags; [] = manual-only
    subject: str = ""                                     # subject line; supports tokens

    blocks: list[Block] = Field(default_factory=list)
    style: Style = Field(default_factory=Style)
    evidence: Evidence = Field(default_factory=Evidence)

    length_min: int = 70                                  # word target for the narrative
    length_max: int = 120
    variables: dict[str, str] = Field(default_factory=dict)   # voice-defined custom tokens
    allow_dashes: bool = False                            # floor knob (default keeps the no-dash rule)
    lead_mode: Literal["news", "noticing"] = "news"
    audience: Literal["self", "organisation"] = "self"   # who this voice speaks FOR
    default_profile_id: str = ""                         # optional profile override; "" = active profile
    recent_point_templates: dict[str, str] = Field(default_factory=dict)  # category -> opener template

    # ---- continuous voice learning (Layer 4) — all additive, defaults = today's behaviour ----
    origin: str = "user"                     # user | learned | challenger — provenance of this voice
    challenger_of: str = ""                  # if set, an A/B challenger tracking this parent voice id
    learning_meta: dict = Field(default_factory=dict)  # {last_cycle_at, edits_since, applied_count}

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data):
        """If handed an old-schema voice (fixed frame fields, no `blocks`), rebuild it as blocks +
        style so pre-existing stored/seed JSON keeps loading."""
        if not isinstance(data, dict):
            return data
        if data.get("blocks") is not None:
            return data
        legacy_markers = ("greeting", "opening", "boilerplate", "close", "body_guidance",
                          "opening_mode", "boilerplate_mode", "close_mode")
        if not any(k in data for k in legacy_markers):
            return data
        d = dict(data)
        d["blocks"] = _canonical_blocks_from_legacy(d)
        style = dict(d.get("style") or {})
        style.setdefault("notes", d.get("register_note") or d.get("register") or "")
        style.setdefault("examples", d.get("examples") or [])
        # fold the old body guidance into a flowing, single-proof default that mirrors house style
        style.setdefault("proof_density", "single")
        style.setdefault("sentence_length", "flowing")
        d["style"] = style
        # Backward-compat: rewrite candidate_evidence / candidate_spine in any existing
        # stored block fact_scopes to the new generic profile_* names (Task H3).
        for blk in d.get("blocks") or []:
            if isinstance(blk, dict):
                blk["fact_scope"] = [
                    "profile_evidence" if s == "candidate_evidence" else
                    "profile_spine" if s == "candidate_spine" else s
                    for s in (blk.get("fact_scope") or [])
                ]
        # Legacy top-level keys from a schema this model no longer has. mention_sci_po,
        # boilerplate_owns_sci_po and close_owns_sci_po used to be set here via setdefault
        # and then popped two lines later -- dead on arrival for every voice that ever
        # passed through this validator, while engine/composition_brief.md kept describing
        # them to the model as live controls. That lie is fixed at task A1; this removes
        # the setdefault call that could never have done anything.
        for k in ("greeting", "opening", "opening_mode", "opening_guidance", "opening_use_recent",
                  "boilerplate", "boilerplate_mode", "boilerplate_guidance", "boilerplate_owns_sci_po",
                  "close_mode", "close_guidance", "close_owns_sci_po", "signoff", "register_note",
                  "register", "body_guidance", "examples", "intro", "preferences", "mention_sci_po"):
            d.pop(k, None)
        return d


class ReplyState(str, Enum):
    """Outcome state of an approved send, driven by the (read-only) inbox sweep.

    awaiting          : staged; no reply or bounce detected yet (the default)
    replied           : a reply matched this send's Message-ID (In-Reply-To/References)
    bounced           : a hard DSN bounce matched; a retry to the next address may be staged
    bounced_exhausted : every known address bounced (or the retry cap was hit)
    """
    awaiting = "awaiting"
    replied = "replied"
    bounced = "bounced"
    bounced_exhausted = "bounced_exhausted"


class AddressCandidate(BaseModel):
    """One rung of the ranked address ladder. `email` is the address; `source` records provenance
    (apollo | research | pattern); `confidence` is a coarse high|medium|low the UI surfaces so the
    operator can see WHY a bounce retry is going where it is going.

    Person fields make the ladder person-aware: a bounce first walks the PRIMARY person's remaining
    formats, then escalates to `alt_person` rungs — a DIFFERENT PERSON at the same target, whose
    name/title the re-draft is then re-addressed to. Defaults keep pre-existing single-person
    ladders (no person data) valid on load and byte-identical in behaviour."""
    email: str
    source: str = "pattern"          # apollo | research | pattern
    confidence: str = "low"          # high | medium | low
    person_name: str = ""            # who this address belongs to (for re-addressing on escalation)
    person_title: str = ""
    tier: str = "primary_person"     # primary_person | alt_person


class SentItem(BaseModel):
    """One row per approved send — the join point for reply detection, bounce handling, the
    pipeline stage, suppression, cost, and per-voice stats. Created at the existing approve hook
    (`_approve_rows`) and stored in `sent_items.json` (mirrors follow_ups.json).

    Off = today: with the inbox disabled these rows are simply recorded and never transition; every
    reader tolerates their absence, so the app is byte-for-byte its current self when nothing sweeps.
    """
    model_config = {"protected_namespaces": ()}

    id: str                                          # f"{slug}#{n}" — unique per send
    slug: str                                        # the draft slug that produced this send
    name: str
    voice: Optional[str] = None
    kind: str = "outreach"                           # outreach | followup | bounce_retry
    step: int = 0                                    # 0 = initial outreach; n = follow-up #n

    message_id: str = ""                             # from _build_eml — the reply/bounce match key
    sent_to: str = ""                                # the address this send actually went to
    to_name: str = ""                                # display name the approved email was addressed to
    address_candidates: list[AddressCandidate] = Field(default_factory=list)  # ranked ladder
    recipient_domain: str = ""
    subject: str = ""
    approved_subject: str = ""                       # the subject as approved (reused on a bounce retry)
    approved_body: str = ""                          # the EXACT approved (edited) email text — reused
    approved_at: str = ""                            #   verbatim on a bounce retry, not recomposed
    source_list_id: str = ""                         # which sourcing list this item entered through

    reply_state: ReplyState = ReplyState.awaiting
    bounce_retry_count: int = 0
    pipeline_flag: str = "none"                      # none | no_response | reopened
    outcome_source: str = "auto"                     # auto (sweep) | manual (operator marked)
    cost_estimate: float = 0.0                       # USD, from token usage at draft time (optional)

    detected_at: Optional[str] = None                # when a reply/bounce last transitioned this row
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Note(BaseModel):
    severity: str                    # "hard" | "soft"  (styling only; neither blocks)
    text: str                        # plain-English, reviewer-facing


class TargetState(BaseModel):
    # identity
    slug: str
    name: str
    website: Optional[str] = None
    recipient_domain: str = ""
    source_list_id: str = ""          # which sourcing list this target entered through; "" = pre-migration
    ref: Optional[str] = None         # display/audit only (e.g. tracker source / category tag)

    state: State = State.input
    error: Optional[str] = None
    voice: Optional[str] = None       # the role-situation voice actually used
    updated_at: Optional[str] = None

    # routing signals research fills (see plan §2.2)
    role_exists: Optional[bool] = None
    company_size: Optional[str] = None      # "small" | "large"

    # pipeline artifacts (all JSON-serialisable)
    cache: Optional[dict[str, Any]] = None          # schema-valid research cache
    spec: Optional[dict[str, Any]] = None           # de.prepare output (frame, tie, provenance)
    subject: Optional[str] = None
    machine_subject: Optional[str] = None

    machine_body: Optional[str] = None              # engine-composed body, normalized (edit anchor)
    machine_email: Optional[str] = None             # original machine draft, full assembled email
    edited_body: Optional[str] = None               # reviewer's body, stored VERBATIM
    edited_email: Optional[str] = None              # full-email edit, stored VERBATIM
    final_email: Optional[str] = None               # assembled email currently shown/approved

    # advisory validation (computed ONCE on the machine draft)
    notes: list[Note] = Field(default_factory=list)
    contact_unverified: bool = False                # the narrow contact-quality flag
    research_capped: bool = False
    disqualified: bool = False                      # work-mode / language hard mismatch
    status_pill: str = ""

    # cost (Phase 1e) — token usage accumulated at draft time, priced per the settings table
    cost_estimate: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0

    # approval
    approved_at: Optional[str] = None
    approver_voice: Optional[str] = None
    approver_os_user: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)  # managed names; [] = use the global default

    def current_body(self) -> str:
        return self.edited_body if self.edited_body is not None else (self.machine_body or "")

    def was_edited(self) -> bool:
        return self.edited_email is not None and self.edited_email != (self.machine_email or "")


# Back-compat alias so code that says CompanyState keeps working unchanged.
CompanyState = TargetState


class BatchState(BaseModel):
    batch_id: str
    voice: Optional[str] = None                    # session default voice (may be overridden per row)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    companies: dict[str, TargetState] = Field(default_factory=dict)  # keyed by slug

    def ordered(self) -> list[TargetState]:
        return list(self.companies.values())


class FollowUpStatus(str, Enum):
    pending = "pending"      # record exists; due_at may be future; no email generated yet
    drafted = "drafted"      # a CompanyState follow-up draft has been generated and is in review
    approved = "approved"    # the follow-up .eml has been staged via the normal approve path
    dismissed = "dismissed"  # the user chose to skip this follow-up


class FollowUp(BaseModel):
    """A first-class follow-up record — the CRM 'tracker' for one pending touch.

    Enrolment is event-driven: approving an outreach email creates the FollowUp for the NEXT step
    (if under the cap). The record carries everything needed to regenerate the follow-up email
    later (parent identity, voice, the original email as context) WITHOUT re-searching. The actual
    email is produced lazily and then flows through the existing draft -> approve -> stage(.eml)
    machinery as a normal CompanyState keyed by `draft_slug` = f"{parent_slug}__f{step}".

    `due_at` is the clock the Follow-ups tab sorts and counts from (original_approved_at + delay).
    The pending/'due' distinction is computed from `due_at` vs now, not stored separately.
    """
    id: str                                    # f"{parent_slug}__f{step}"
    parent_slug: str
    name: str
    website: Optional[str] = None
    contact_email: str = ""
    contact_name: str = ""
    voice: Optional[str] = None
    step: int = 1                              # 1 = first follow-up
    original_subject: str = ""
    original_body: str = ""                    # the approved original email — context for the model
    original_approved_at: str = ""             # ISO — the clock the tab sorts/counts from
    due_at: str = ""                           # ISO — original_approved_at + delay[step-1]
    status: FollowUpStatus = FollowUpStatus.pending
    origin_message_id: str = ""                # Message-ID of the original send (reply pauses cadence)
    draft_slug: Optional[str] = None           # the CompanyState slug once generated
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CustomSourcingPrompt(BaseModel):
    """A saved sourcing criteria definition — the sourcing-stage analogue of CustomVoice.
    Seeded once (a small starter set), then fully user-owned: editable, duplicable, deletable.
    """
    model_config = {"protected_namespaces": ()}

    id: str
    display_name: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    seeded_from: str = "blank"

    criteria_text: str = ""
    sources: list[str] = Field(default_factory=lambda: ["techeu_funding_feed", "grounded_search"])
    recency_days: int = 120
    target_n: int = 0                            # accepted-candidate goal for a run; 0 = use global setting
    exclude_notes: str = ""

    # ---- Stage F / G1: local screening gates (typed, all optional) ----
    revenue_band_min: int | None = None           # minimum annual revenue (USD)
    revenue_band_max: int | None = None           # maximum annual revenue (USD)
    require_keyword_in_field: dict[str, str] = Field(default_factory=dict)   # {field: keyword}
    reject_last_event_types: list[str] = Field(default_factory=list)         # e.g. ["acquisition"]

    # ---- G3: exclusion policy ----
    exclusion_policy: Literal["permanent", "expiring"] = "permanent"

    last_run_at: str = ""
    total_candidates_seen: int = 0
