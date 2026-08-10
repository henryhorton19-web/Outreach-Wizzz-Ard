"""The link matcher is a PRECISION stage over the domain matcher's RECALL stage.

Precedent: a reranker "can only reorder what retrieval hands it -- treat it as a
precision layer on top of a strong retrieval layer, not a substitute for one."
So the domain matcher still runs, still narrows, and the LLM only ever sees a
shortlist. A failed or disabled reranker degrades to the keyword result, never to
an unlinked email and never to a crash.
"""
import engine.draft_engine as de
import app.link_matcher as lm


class _StubProvider:
    is_stub = False
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate(self, **kw):
        self.calls += 1
        class R: text = self.payload
        return R()


def _cache():
    return {"company": {"name": "Example SaaS", "what_they_do": "platform for private market funds"},
            "situation_read": "onboarding more funds to automate deal flow"}


def _exps():
    return [
        {"_key": "hpe_app", "name": "Shipped sourcing app", "anchor": "a",
         "bridges": ["builds"], "domains": ["private_markets", "sourcing_automation"]},
        {"_key": "hpe_dd", "name": "Diligence", "anchor": "b",
         "bridges": ["analytical"], "domains": ["private_markets", "saas_metrics"]},
        {"_key": "policy", "name": "Bright Blue", "anchor": "c",
         "bridges": ["analytical"], "domains": ["policy"]},
    ]


def test_shortlist_is_capped_at_three():
    """Generation accuracy saturates around 5-10 contexts; with six experiences the
    right shortlist is 3. Larger buys nothing, smaller risks dropping the answer."""
    short = lm.shortlist(_exps() * 4, _cache(), limit=3)
    assert len(short) == 3


def test_the_shortlist_is_ordered_by_the_domain_matcher():
    short = lm.shortlist(_exps(), _cache(), limit=3)
    assert short[0]["_key"] in ("hpe_app", "hpe_dd"), \
        f"recall stage did not put a private-markets experience first: {short[0]['_key']}"


def test_matcher_returns_a_structured_link():
    prov = _StubProvider('{"link_strength":"strong","experience_keys":["hpe_app"],'
                         '"shared_subject":"sourcing automation for private markets",'
                         '"why":"built the same thing inside a fund","confidence":0.9}')
    out = lm.resolve_link(_cache(), _exps(), provider=prov)
    assert out["link_strength"] == "strong"
    assert out["experience_keys"] == ["hpe_app"]
    assert out["shared_subject"]
    assert prov.calls == 1


def test_a_failed_call_degrades_to_the_keyword_result(monkeypatch):
    class Boom:
        is_stub = False
        def generate(self, **kw): raise RuntimeError("provider down")
    out = lm.resolve_link(_cache(), _exps(), provider=Boom())
    assert out["link_strength"] in ("strong", "weak", "none")
    assert out.get("source") == "keyword_fallback", \
        "a failed reranker must degrade to the recall stage, not crash or return nothing"


def test_the_stub_provider_never_calls_out():
    class Stub:
        is_stub = True
        def generate(self, **kw): raise AssertionError("must not be called")
    out = lm.resolve_link(_cache(), _exps(), provider=Stub())
    assert out.get("source") == "keyword_fallback"


def test_an_invented_experience_key_is_rejected():
    """The matcher may only cite experiences it was shown."""
    prov = _StubProvider('{"link_strength":"strong","experience_keys":["does_not_exist"],'
                         '"shared_subject":"x","why":"y","confidence":0.9}')
    out = lm.resolve_link(_cache(), _exps(), provider=prov)
    assert out["link_strength"] != "strong", \
        "matcher cited an experience that was never in the shortlist"


def test_the_reranker_cannot_promote_above_the_recall_stage():
    """The architectural invariant. A reranker reorders and downgrades what retrieval
    hands it; it never promotes above it. Verified failure without this: a bakery
    returned "strong" because the model asserted it, despite zero domain overlap."""
    unrelated = {"company": {"name": "Crust", "what_they_do": "artisan bakery chain"},
                 "situation_read": "opening new sites"}
    prov = _StubProvider('{"link_strength":"strong","experience_keys":["hpe_app"],'
                         '"shared_subject":"baking","why":"both involve process","confidence":0.95}')
    out = lm.resolve_link(unrelated, _exps(), provider=prov)
    assert out["link_strength"] != "strong", \
        "the reranker promoted a link the recall stage found no basis for"


def test_low_confidence_is_downgraded():
    prov = _StubProvider('{"link_strength":"strong","experience_keys":["hpe_app"],'
                         '"shared_subject":"x","why":"y","confidence":0.2}')
    out = lm.resolve_link(_cache(), _exps(), provider=prov)
    assert out["link_strength"] != "strong", \
        "a low-confidence claim was accepted as strong"


def test_second_draft_reuses_cached_candidate_link():
    c = _cache()
    prov = _StubProvider('{"link_strength":"strong","experience_keys":["hpe_app"],'
                         '"shared_subject":"x","why":"y","confidence":0.9}')
    c["candidate_link"] = lm.resolve_link(c, _exps(), provider=prov)
    assert prov.calls == 1
    spec = de.prepare(c)
    assert spec["link_strength"] == "strong"
    assert spec["link"]["shared_subject"] == "x"
    assert prov.calls == 1, "second preparation must reuse candidate_link without second provider call"
