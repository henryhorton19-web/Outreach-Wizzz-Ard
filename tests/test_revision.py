"""Generate, measure, revise, keep the better one.

Self-Refine improves outputs by about 20% absolute across seven tasks because it
is easier for a model to fix a draft it can see than to write a perfect one
first. Refinement is not monotonic, so the loop keeps whichever version scores
better rather than assuming the revision wins.
"""
import app.revise as rv


class _Stub:
    is_stub = False
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate(self, **kw):
        self.calls += 1
        class R: text = self.payload
        return R()


AVYN = ("Having judged companies from the investor side and also built sourcing pipelines and "
        "automation for private markets myself, Example SaaS really stood out to me.")


def _ctx():
    return {"style_examples": [AVYN],
            "observation": "holding a no-technical-friction promise as agents multiply",
            "proof_points": [], "situation_read": ""}


def test_a_clean_draft_is_not_revised():
    p = _Stub("should not be called")
    body = "Holding a no-technical-friction promise as agents multiply is the hard part for you."
    out = rv.revise_if_needed(body, _ctx(), provider=p)
    assert out == body and p.calls == 0, "a clean draft triggered a needless revision call"


def test_a_flawed_draft_is_revised_once():
    better = "Holding a no-technical-friction promise as agents multiply looks like your hard part."
    p = _Stub(better)
    body = ("Having judged companies from the investor side and also built an LLM pipeline, "
            "Webyn really stood out to me.")
    out = rv.revise_if_needed(body, _ctx(), provider=p)
    assert out == better and p.calls == 1


def test_a_worse_revision_is_discarded():
    """Refinement is not monotonic. If the revision scores strictly worse, keep the
    original.

    Note the construction: `body` must have exactly ONE outstanding instruction and
    the revision must have TWO, so the comparison is strict rather than a tie. On a
    tie the revision wins by design, since it was written knowing more. A test that
    supplies an equally-bad revision is testing the tie rule, not this one.
    """
    # body: uses the observation, so only the scaffold note fires -> score -1
    body = ("Having judged companies from the investor side and also built things, holding a "
            "no-technical-friction promise as agents multiply is your hard part.")
    # revision: reuses the scaffold AND drops the observation -> score -2
    worse = ("Having judged companies from the investor side and also built sourcing pipelines and "
             "automation for private markets myself, it really stood out to me.")
    p = _Stub(worse)
    out = rv.revise_if_needed(body, _ctx(), provider=p)
    assert out == body, "a strictly worse revision was kept"


def test_a_provider_failure_returns_the_original():
    class Boom:
        is_stub = False
        def generate(self, **kw): raise RuntimeError("down")
    body = "Having judged companies from the investor side and also built things, X stood out."
    assert rv.revise_if_needed(body, _ctx(), provider=Boom()) == body
