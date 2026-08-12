# EXECUTION PLAN 25: REVAMP WIZZ-ARD ADEPT

**Repository:** `https://github.com/henryhorton19-web/paris-outreach`
**Companion report:** `REPORT_WIZZARD_ADEPT_TEARDOWN.md`. Read Part 3 before starting.
**Diagnosed from:** batch `f23f72eef771`, two real drafts (Partoo, Ouihelp), both `wizzard_adept`.

---

## PART A: BASELINE, AND WHY IT IS NOT PINNED TO A COMMIT

`app/draft_shape.py` and `app/seed_voices/wizzard_adept.json` **do not exist on `origin/main`**, verified by
fresh clone. They exist only on a local branch. So this plan cannot pin a base commit the way earlier ones
did.

**Establish your own baseline instead and record it:**
```bash
cd <path-to>/paris-outreach
git rev-parse --short HEAD
git branch --show-current
git status --porcelain
rm -rf .test_data && python -m pytest tests/ -q | tail -2
python - <<'EOF'
import pathlib
for f in ("app/seed_voices/wizzard_adept.json", "app/draft_shape.py",
          "app/observation_sampler.py", "app/observation_quality.py"):
    print(f"{f:44} exists={pathlib.Path(f).exists()}")
EOF
```
**Record the pass count and the four existence flags.** Every `EXPECT` below refers to your recorded count,
written as `BASELINE`.

**IF `wizzard_adept.json` does not exist → STOP.** This plan revamps that voice; it does not create it. Run
Plan 24 Stage 4 first.

---

## PART B: RULES

1. Tasks run in numerical order.
2. `FIND` text must match exactly and appear once. **Two anchors are verified against `origin/main`; the
   rest are marked `[LOCATE]` because the files are local-only.** On a mismatch, stop and report the task.
3. Run every `VERIFY`. Compare to `EXPECT`. On a mismatch, stop and report.
4. Change nothing a task does not name. **Do not modify `wizzard_default.json`.**
5. Never run the application.
6. **Tasks 1, 4, 7 and 10 are designed to fail.** Do not fix them early.
7. Commit where told. Do not push.

---

# STAGE 1: THE SIGNATURE (Tasks 1 to 3)

**Highest measured return in the plan, and the smallest change.** A regression over 100,000 AI-sent emails
found that a signature carrying name, title and link lifts reply rate by **9%**, and that most AI prompts
ship without signature templating, making it a one-line fix worth more than most copy optimisation. The two
observed drafts have no signature at all.

## TASK 1: Failing test

**CREATE `tests/test_signature.py`:**
```python
"""Every draft must end with a signature the reader can verify.

Both observed drafts ended on "Open to a quick chat?" with no surname, no
identity line and no link. A recipient cannot tell whether the sender is a
student, a founder or an agency, and has nothing to click.

Measured: a signature carrying name, title and link lifts reply rate by 9%, and
it is one line of templating.
"""
import engine.draft_engine as de


def _spec(**kw):
    base = {"greeting": "Hi Thibault,", "ask": "Open to a quick chat?",
            "opening_fallback": "", "allow_dashes": False, "link_strength": "none",
            "allowed_facts": [], "allowed_numbers": [], "lead_mode": "noticing",
            "recent": {"present": False},
            "signature": "Henry Horton\nLSE, on exchange at Sciences Po\nlinkedin.com/in/henryhorton"}
    base.update(kw)
    return base


def test_the_signature_is_appended():
    out = de.finalize(_spec(), {"body": "A body."})
    assert "Henry Horton" in out["email"]
    assert "linkedin.com" in out["email"]


def test_the_signature_comes_last():
    out = de.finalize(_spec(), {"body": "A body."})
    assert out["email"].strip().endswith("linkedin.com/in/henryhorton")


def test_no_signature_configured_still_assembles():
    """A voice without one must not break, and must not print an empty block."""
    out = de.finalize(_spec(signature=""), {"body": "A body."})
    assert out["email"].strip().endswith("Open to a quick chat?")
    assert "\n\n\n" not in out["email"]


def test_the_signature_is_not_dash_normalised_into_nonsense():
    out = de.finalize(_spec(signature="Henry Horton\nLSE - Sciences Po"), {"body": "A body."})
    assert "Henry Horton" in out["email"]
```

**VERIFY:** `rm -rf .test_data && python -m pytest tests/test_signature.py -q 2>&1 | tail -5`
**EXPECT:** the first two fail, the last two pass.

---

## TASK 2: Append it in `finalize`

**FILE:** `engine/draft_engine.py`

**FIND** (verified unique against `origin/main`):
```python
    blocks = [greeting, opening, body, ask_block]
```
**REPLACE WITH:**
```python
    # A signature carrying name, identity and a link is the cheapest measured win
    # available: roughly +9% reply rate for one line of templating, and both
    # observed drafts shipped without one. A recipient who cannot tell whether the
    # sender is a student, a founder or an agency, and has nothing to click, has no
    # reason to book a call.
    signature = (spec.get("signature") or "").strip()
    blocks = [greeting, opening, body, ask_block, signature]
```

**Note:** the existing `join` filters empty strings, so a voice with no signature is unaffected. Confirm
that by reading the next line before proceeding.

**VERIFY:**
```bash
python -c "import ast; ast.parse(open('engine/draft_engine.py').read()); print('syntax ok')"
rm -rf .test_data && python -m pytest tests/test_signature.py -q 2>&1 | tail -3
rm -rf .test_data && python -m pytest tests/ -q 2>&1 | tail -2
```
**EXPECT:** `syntax ok`, `4 passed`, suite at BASELINE plus 4.

---

## TASK 3: Carry it from the voice and profile

**FILE:** `app/models.py`, on `CustomVoice`. `[LOCATE]` `grep -n 'allow_dashes: bool' app/models.py` and add
after it:
```python
    # Rendered last, after the ask. Empty means no signature block.
    signature: str = ""
```

**FILE:** `engine/draft_engine.py`, in `prepare()`. `[LOCATE]` the line building `"ask"` and add beside it:
```python
        "signature": spec_voice.get("signature", ""),
```

**FILE:** `app/seed_voices/wizzard_adept.json`. Add:
```json
"signature": "Henry Horton\nLSE Politics and Economics, on exchange at Sciences Po Paris\nlinkedin.com/in/henry-horton"
```
**Replace the LinkedIn slug with your real one.** A dead link is worse than none, because it is checkable.

**ACCEPT:** a draft under `wizzard_adept` ends with three lines: name, identity, link. A draft under
`wizzard_default` is unchanged.

## COMMIT STAGE 1

---

# STAGE 2: FIX THE IDENTICAL PROPOSAL (Tasks 4 to 6)

**A reproduced scoring bug, not a prompt problem.**

`engine/draft_engine.py:203` adds a flat `+1` to whichever experience is `standing_key`, on every company.
With `standing_key: "outreach_system"` and the voice setting `evidence.count: 1`, that experience wins
everywhere:

```
Partoo  (local marketing SaaS):   2  outreach_system   -> picks outreach_system
Ouihelp (elderly care):           2  outreach_system   -> picks outreach_system
any company at all:               2  outreach_system   -> picks outreach_system
```

Which is why both emails proposed building an outreach system, to a marketing SaaS and an elderly-care
provider.

## TASK 4: Failing test

**CREATE `tests/test_evidence_variety.py`:**
```python
"""Two different companies must not always draw the same experience.

standing_key adds a flat +1 to one experience on every company. Combined with
evidence.count = 1, that experience wins everywhere, so every email proposed the
same thing regardless of what the company does.
"""
import engine.draft_engine as de


def _cache(what, situation=""):
    return {"company": {"name": "X", "what_they_do": what, "role_exists": False,
                        "company_size": "small"},
            "contact": {"name": "A", "email": "a@x.com"},
            "situation_read": situation, "proof_points": []}


def test_standing_key_does_not_dominate_a_mismatched_target():
    """An elderly care provider should not draw an outreach-automation project
    purely because it is the standing experience."""
    marketing = de.select_evidence(_cache("local marketing SaaS for retail chains"), count=2)
    care = de.select_evidence(_cache("at home elderly care with 8000 caregivers"), count=2)
    assert {e["_key"] for e in marketing} != {e["_key"] for e in care} or len(marketing) > 1, \
        "identical single evidence for two unrelated businesses"


def test_count_two_returns_two_when_available():
    picked = de.select_evidence(_cache("software company"), count=2)
    assert len(picked) >= 2 or len(de.rank_evidence(_cache("software company"))) < 2


def test_standing_bonus_is_capped_below_a_real_match():
    """The standing bonus must be a tiebreak, not a decider. A domain or bridge
    match should outrank it."""
    src = open("engine/draft_engine.py", encoding="utf-8").read()
    assert "score += 1" not in src.split("standing_key")[1][:200] or "STANDING_BONUS" in src, \
        "the standing bonus is still a bare +1 with no cap"
```

**VERIFY:** `rm -rf .test_data && python -m pytest tests/test_evidence_variety.py -q 2>&1 | tail -5`
**EXPECT:** at least the third fails.

---

## TASK 5: Demote the standing bonus

**FILE:** `engine/draft_engine.py`

**FIND** (verified unique against `origin/main`):
```python
        standing = C.CANDIDATE_PROFILE.get("standing_key", "anchor_co")
        if key == standing:
            score += 1
```
**REPLACE WITH:**
```python
        # The standing experience is a TIEBREAK, not a decider. As a flat +1 it beat
        # every genuine signal: with evidence.count = 1 it won on every company, so
        # an outreach-automation project was proposed to a local-marketing SaaS and
        # to an elderly-care provider in the same batch. A fractional bonus breaks
        # ties among equals without overriding a real match.
        STANDING_BONUS = 0.25
        standing = C.CANDIDATE_PROFILE.get("standing_key", "anchor_co")
        if key == standing:
            score += STANDING_BONUS
```

**Confirm `score` is not typed as `int` elsewhere** in the same function before making it fractional:
```bash
grep -n 'score' engine/draft_engine.py | sed -n '1,20p'
```
If the sort or any comparison assumes an integer, adjust that rather than reverting this change.

**VERIFY:**
```bash
python -c "import ast; ast.parse(open('engine/draft_engine.py').read()); print('syntax ok')"
rm -rf .test_data && python -m pytest tests/ -q 2>&1 | tail -2
```
**EXPECT:** `syntax ok`, suite at BASELINE plus the new tests, no regressions.

---

## TASK 6: Raise the evidence count on the voice

**FILE:** `app/seed_voices/wizzard_adept.json`

`[LOCATE]` the `evidence` block and set `"count": 2`.

**Why not leave it at 1.** With one experience the proposal has only one possible shape. With two, the
composer can pair an analytical credential with a build, which is the combination that is actually unusual
about this candidate. Two is also the historic default elsewhere in the codebase.

**ACCEPT:** drafting a marketing SaaS and an elderly-care provider selects different evidence sets, or at
minimum two experiences rather than one.

## COMMIT STAGE 2

---

# STAGE 3: THE OPENER MUST CARRY A NUMBER (Tasks 7 to 9)

**Both observed openers hit a named 2026 anti-pattern.** The published avoid-list includes *"I noticed
[company] is growing quickly. Means nothing. Every B2B company says they are growing. The line provides
zero signal of real research."*

- Partoo: *"this seems like a pivotal moment for operational scaling"*
- Ouihelp: *"point to a period of aggressive expansion in the home care sector"*

Both are growth-trend statements. And the 100,000-email regression found penalties **concentrate at the
opener**, so this is the most expensive line in the email to get wrong.

The existing `draft_shape.py` check accepts any distinctive word, so *"7,500 clients"* inside a subordinate
clause satisfied it while the sentence still said nothing.

## TASK 7: Failing test

**CREATE `tests/test_opener_substance.py`:**
```python
"""The first sentence must carry a figure or a named thing, not a growth trend.

The published 2026 avoid-list names "I noticed [company] is growing quickly" as
providing zero signal of real research, and a 100k-email regression found reply
penalties concentrate at the opener rather than the body.
"""
import app.opener_check as oc


def test_a_growth_trend_opener_is_flagged():
    for body in [
        "This seems like a pivotal moment for operational scaling.",
        "Your recent acquisitions point to a period of aggressive expansion.",
        "As you scale rapidly across Europe, complexities will grow.",
    ]:
        assert oc.opener_notes(body, {"proof_points": []}), f"not flagged: {body!r}"


def test_an_opener_with_a_figure_passes():
    body = "Going from 35.5m ARR to a 100m target while staying near breakeven is the whole year."
    assert oc.opener_notes(body, {"proof_points": [{"fact": "35.5m ARR"}]}) == []


def test_an_opener_naming_a_specific_thing_passes():
    body = "Merging Ouihelp, Joya and ONELA onto one caregiver system is the hard part."
    assert oc.opener_notes(body, {"proof_points": [{"fact": "three brands"}]}) == []


def test_a_figure_buried_in_a_subordinate_clause_is_still_flagged():
    """"With over 7,500 clients, this seems like a pivotal moment" uses the number
    as scenery propping up a generic conclusion."""
    body = "With over 7,500 clients, this seems like a pivotal moment for operational scaling."
    assert oc.opener_notes(body, {"proof_points": [{"fact": "7,500 clients"}]})


def test_the_note_names_an_available_figure():
    notes = oc.opener_notes("You are growing quickly.",
                            {"proof_points": [{"fact": "35.5m ARR against a 100m target"}]})
    assert any("35.5m" in n for n in notes)
```

**VERIFY:** `rm -rf .test_data && python -m pytest tests/test_opener_substance.py -q 2>&1 | tail -5`
**EXPECT:** all five fail.

---

## TASK 8: Build the opener check

**`[NEW] app/opener_check.py`:**
```python
"""Does the first sentence say anything only true of this company?

The published 2026 opener avoid-list names "I noticed [company] is growing
quickly" as providing zero signal of real research, because every B2B company is
growing. A 100,000-email regression found reply-rate penalties concentrate at the
opener rather than in the body, which makes this the most expensive line to get
wrong.

Returns revision instructions naming an unused figure, never a verdict. If no
figure is available it returns nothing: an honest generic opener outperforms a
fake situational one, so a company with no public numbers should not be forced
into inventing a specific.
"""
from __future__ import annotations

import re

# Growth-trend language. True of everyone, therefore informationally empty.
_TREND = (
    "growing quickly", "growing fast", "rapid growth", "rapid expansion",
    "aggressive expansion", "pivotal moment", "exciting growth", "period of growth",
    "as you scale", "as you grow", "scaling rapidly", "exciting time",
    "point to a period", "signals a new phase", "signal an exciting",
)

# Hedges that mark the sentence as a guess rather than an observation.
_HEDGE = ("seems like", "must be", "will likely", "likely be", "appears to be",
          "i imagine", "presumably", "no doubt")

_FIGURE = re.compile(r"\d")


def _available_figures(ctx: dict) -> list[str]:
    out = []
    for p in (ctx.get("proof_points") or []):
        s = p.get("fact", "") if isinstance(p, dict) else str(p)
        if s.strip() and _FIGURE.search(s):
            out.append(s.strip())
    return out


def _first_sentence(body: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (body or "").strip())
    return parts[0] if parts else ""


def opener_notes(body: str, ctx: dict) -> list[str]:
    """Instructions for the first sentence. Empty means it is doing its job."""
    first = _first_sentence(body)
    if not first:
        return []
    low = first.lower()
    notes: list[str] = []

    hits_trend = [t for t in _TREND if t in low]
    hits_hedge = [h for h in _HEDGE if h in low]
    has_figure = bool(_FIGURE.search(first))

    # A figure present but sitting alongside trend language is being used as
    # scenery: "With over 7,500 clients, this seems like a pivotal moment."
    scenery = has_figure and (hits_trend or hits_hedge)

    if hits_trend or hits_hedge or scenery or not has_figure:
        figures = _available_figures(ctx)
        if not figures:
            # No numbers available. An honest generic opener beats a fake specific
            # one, so do not demand something the research cannot support.
            if hits_hedge:
                notes.append(
                    f"Your first sentence guesses at their situation ({hits_hedge[0]!r}). No figures "
                    f"were found for this company, so say plainly what you do and why you wrote, "
                    f"rather than inventing an observation.")
            return notes[:1]

        reason = ("uses a growth trend that is true of every company"
                  if hits_trend else
                  "guesses at their situation" if hits_hedge else
                  "uses a number as scenery around a generic conclusion" if scenery else
                  "contains no figure or named thing")
        notes.append(
            f"Your first sentence {reason}. Rewrite it so the figure IS the sentence, not the "
            f"setup: build it on {figures[0]!r}. The opener is where reply-rate penalties "
            f"concentrate, so it has to carry the one thing only true of them.")
    return notes[:1]
```

**VERIFY:**
```bash
python -c "import ast; ast.parse(open('app/opener_check.py').read()); print('syntax ok')"
rm -rf .test_data && python -m pytest tests/test_opener_substance.py -q 2>&1 | tail -3
```
**EXPECT:** `syntax ok`, `5 passed`.

---

## TASK 9: Feed it into the revision pass

**IF `app/draft_shape.py` exists on your branch**, add `opener_notes` to whatever function assembles its
instruction list, so the opener instruction travels with the others through the existing single revision
pass. `[LOCATE]` `grep -n 'def shape_notes' app/draft_shape.py`.

**IF it does not exist**, call `opener_notes` from wherever the body is composed in `app/compose.py`, gated
on `voice.variables.get("use_shape_guidance") == "true"`, and make one revision call when it returns
anything. Keep whichever version has fewer outstanding notes; refinement is not monotonic.

**Cap the combined instruction list at three.** A revision prompt carrying six complaints produces worse
output than one carrying two.

**ACCEPT:** a draft opening *"this seems like a pivotal moment"* is rewritten to lead with a figure. A draft
already leading with a figure is untouched and costs no revision call.

## COMMIT STAGE 3

---

# STAGE 4: THE PROPOSAL MUST BE ABOUT THEIR BUSINESS (Tasks 10 to 12)

Stage 2 fixes *which* experience is selected. This fixes what the paragraph does with it. Both observed
proposals described rebuilding the sender's own last project inside the recipient's company.

## TASK 10: Failing test

**CREATE `tests/test_proposal_relevance.py`:**
```python
"""The proposal must act on the recipient's business, not restage the sender's
last project.

Both observed drafts proposed building an outreach system: to a local-marketing
SaaS with 7,500 clients, and to an elderly-care provider with 8,000 caregivers.
Neither asked for one.
"""
import app.proposal_check as pc


def test_restaging_the_senders_own_project_is_flagged():
    body = ("My first step would be to build the system to find companies, research them, and write "
            "the outreach, which is work I have done before at Example Capital.")
    ctx = {"what_they_do": "local marketing SaaS for retail chains", "proof_points": []}
    notes = pc.proposal_notes(body, ctx)
    assert notes, "a proposal unrelated to the recipient's business was not flagged"


def test_a_proposal_grounded_in_their_business_passes():
    body = ("The first thing I would look at is which onboarding step loses the most salons in "
            "week one.")
    ctx = {"what_they_do": "booking platform for salons", "proof_points": []}
    assert pc.proposal_notes(body, ctx) == []


def test_an_employer_name_used_as_a_credential_is_flagged():
    body = "I would build the pipeline, just as I did for Example Capital."
    ctx = {"what_they_do": "elderly care", "proof_points": []}
    assert any("credential" in n.lower() or "lead with" in n.lower()
               for n in pc.proposal_notes(body, ctx))


def test_no_context_returns_nothing():
    assert pc.proposal_notes("A proposal.", {}) == []
```

**VERIFY:** `rm -rf .test_data && python -m pytest tests/test_proposal_relevance.py -q 2>&1 | tail -5`
**EXPECT:** all four fail.

---

## TASK 11: Build the proposal check

**`[NEW] app/proposal_check.py`:**
```python
"""Is the proposal about their business, or about the sender's last project?

Both observed drafts proposed building an outreach system, to a local-marketing
SaaS and to an elderly-care provider. Neither asked for one. The sender described
restaging his own most recent project inside their company, which tells the reader
he decided what he would do before finding out what they need.

Detection is deliberately narrow: it looks for the sender's own project vocabulary
appearing in a proposal to a company whose stated business shares none of it.
Narrow because a false flag here would suppress a legitimate proposal, and a
missed one merely leaves the draft as it is.
"""
from __future__ import annotations

import re

# The sender's own tooling vocabulary. A proposal built from these words, to a
# company that does none of it, is a restaging rather than an offer.
_OWN_PROJECT = (
    "build the system to find", "find companies, research", "find, research",
    "sourcing pipeline", "outreach system", "the system to find companies",
    "write the outreach",
)

# Employer names used as a credential rather than as evidence of an action.
_CREDENTIAL = ("just as i did for", "which is work i have done before at",
               "as i did at", "which i did at")


def proposal_notes(body: str, ctx: dict) -> list[str]:
    """Instructions for the proposal paragraph. Empty means it is fine."""
    what = str((ctx or {}).get("what_they_do") or "").strip()
    if not what:
        return []                      # no way to judge relevance; say nothing

    low = (body or "").lower()
    notes: list[str] = []

    if any(p in low for p in _OWN_PROJECT):
        # Does their business plausibly involve outbound at all?
        theirs = set(re.findall(r"[a-z]{4,}", what.lower()))
        outbound = {"sales", "outbound", "outreach", "prospecting", "crm", "leads",
                    "marketing", "growth", "pipeline"}
        if not (theirs & outbound):
            notes.append(
                f"You are proposing to build your own last project inside their company. They do "
                f"{what!r}, which involves none of that. Name one thing you would do about THEIR "
                f"business instead, drawn from what research found about them.")

    if any(c in low for c in _CREDENTIAL):
        notes.append(
            "You are using an employer name as a credential. Lead with the action and let the "
            "experience sit behind it, rather than the other way round.")

    return notes[:2]
```

**VERIFY:**
```bash
python -c "import ast; ast.parse(open('app/proposal_check.py').read()); print('syntax ok')"
rm -rf .test_data && python -m pytest tests/test_proposal_relevance.py -q 2>&1 | tail -3
```
**EXPECT:** `syntax ok`, `4 passed`.

---

## TASK 12: Wire it in, and rewrite the block guidance

**Wire it** into the same instruction list as Task 9, keeping the combined cap at three.

**FILE:** `app/seed_voices/wizzard_adept.json`. Replace the `proposal` block's guidance with:

```
Name the ONE thing you would do about THEIR business in your first week. It must follow from what you
noticed about them, not from what you happen to have built before.

Ground it in something you have actually done, but the action comes first and the experience sits behind
it. Do not name your employer as a credential; if the experience is relevant, describing the work is
enough.

Never propose rebuilding a project of your own inside their company unless their business is genuinely the
same shape as it.

If there is no honest link between your background and their business, say so in one short clause and
stop. A short honest email outperforms a padded one, and a generic honest opener outperforms a fake
specific one.

One or two sentences.
```

**ACCEPT:** drafting an elderly-care provider and a marketing SaaS produces two different proposals, and
neither proposes building an outreach system unless the company's own business involves outbound.

## COMMIT STAGE 4

---

# STAGE 5: CUT THE GENERATION WASTE (Task 13)

**Measured on the two observed runs:**

| Company | Input | Output | Cost | Words shipped | Output tokens per shipped word |
|---|---|---|---|---|---|
| Partoo | 7,306 | 9,466 | $0.0271 | ~65 | 146 |
| Ouihelp | 7,433 | 12,793 | $0.0354 | ~65 | 197 |

A 65-word email is roughly 85 tokens, so **over 99% of generated output is discarded.**

## TASK 13: Find and reduce it

**This is a measurement task before it is a change.** `[LOCATE]` every generation call on the draft path:
```bash
grep -rn 'provider.generate' app/*.py | grep -v __pycache__
```

For each, record what it generates and whether its output survives into the email. Expect to find stacked
stages from earlier plans: an observation sampler producing K candidates, a body sampler producing K more,
and a revision pass regenerating a full body.

**Then reduce in this order, re-measuring after each:**

1. **Lower K on the body sampler to 3.** Published diversity gains do not require a large K, and each
   candidate is a full body.
2. **Skip the revision call when the instruction list is empty.** Verify this is already true; if a
   revision fires on a clean draft, that is pure waste.
3. **Do not run both an observation sampler and a separate observation resolver.** `grep` for both
   `sample_observations` and `resolve_observation` on the same path. If both run, one is redundant.

**VERIFY:** draft one company and record `tokens_out` and `cost_estimate` from `drafts.json` before and
after. **EXPECT** a material reduction. **Report the actual numbers rather than asserting an improvement.**

**Do not reduce below the point where output quality changes.** Re-read a draft after each reduction. This
is a cost task, not a quality task, and quality wins on a conflict.

## COMMIT STAGE 5

---

## APPENDIX: FULL VERIFICATION

```bash
rm -rf .test_data && python -m pytest tests/ -q 2>&1 | tail -2
python -m pytest tests/test_signature.py tests/test_evidence_variety.py \
                 tests/test_opener_substance.py tests/test_proposal_relevance.py -q 2>&1 | tail -2
for t in check_markup check_selectors check_contrast check_style_purity check_vocabulary; do
  python tools/$t.py >/dev/null 2>&1; echo "$t=$?"; done
node --check ui/app.js; echo "js=$?"
git diff --stat app/seed_voices/wizzard_default.json
```
**EXPECT:** suite at BASELINE plus the new tests, all gates `0`, `js=0`, and **no diff on
`wizzard_default.json`**.

```
REVIEWER GATE: REQUIRES A HUMAN

Re-draft Partoo and Ouihelp, then read them side by side.

1. Both end with a signature: name, identity line, working link.
2. Neither opener contains "pivotal moment", "aggressive expansion", "as you scale" or
   "seems like".
3. Each opener leads with a figure or a named thing. For Partoo that should be the ARR
   number or the 100m target; for Ouihelp the caregiver count, the three brands, or the
   fact the last raise was 2022.
4. The two proposals are DIFFERENT from each other, and neither proposes building an
   outreach system.
5. Neither says "just as I did for Example Capital" or similar.
6. Record tokens_out and cost for each. Compare against $0.0271 and $0.0354.
7. The honest case: draft a company with no public figures. The opener should be plainly
   honest rather than a manufactured observation, and the email should be short.
8. wizzard_default, drafted on the same company, is unchanged from before this plan.

Status: not verified, awaiting review
```

## PRINCIPLES

1. **Fix the edges first.** Reply-rate penalties concentrate at the opener, the closer and the missing
   signature, not in the body, so that is where the cheapest wins are.
2. **The signature is one line of templating for roughly 9%.** Nothing else in this plan has that ratio.
3. **A standing experience is a tiebreak, not a decider.** A flat bonus becomes a pin the moment the
   evidence count is 1.
4. **A growth trend is not an observation.** It is true of every company, so it carries no research signal
   however fluently it is written.
5. **A figure must be the sentence, not the setup.** A number in a subordinate clause propping up a generic
   conclusion is scenery.
6. **Propose about their business.** Restaging your own last project tells the reader you decided before
   you looked.
7. **When there are no figures, be honestly generic.** A generic honest opener outperforms a fake
   situational one, so never force a specific the research cannot support.
8. **The penalty is about perceived effort, not prose quality.** Short and empty reads worse than long and
   specific, which is why the previous shortening alone did not help.
