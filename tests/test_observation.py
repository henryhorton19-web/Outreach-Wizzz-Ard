"""One sharp, non-transferable observation per target, generated from research.

Isolating it makes the creative act checkable: a single sentence can be tested
for transferability where a whole email cannot.
"""
import app.observation as obs


class _Stub:
    is_stub = False
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate(self, **kw):
        self.calls += 1
        class R: text = self.payload
        return R()


def _cache():
    return {"company": {"name": "Webyn", "what_they_do": "AI conversion rate optimisation platform"},
            "situation_read": "scaling AI agents while keeping a no technical friction promise",
            "proof_points": [{"fact": "increases conversion rates by an average of 30%"}]}


def test_returns_structured_observation():
    p = _Stub('{"observation":"holding a no-technical-friction promise as agents multiply",'
              '"transferable":false,"confidence":0.8}')
    out = obs.resolve_observation(_cache(), provider=p)
    assert out["observation"] and p.calls == 1


def test_transferable_observation_is_discarded():
    p = _Stub('{"observation":"they are focused on growth","transferable":true,"confidence":0.9}')
    assert not obs.resolve_observation(_cache(), provider=p)["observation"]


def test_stub_provider_returns_empty():
    class S:
        is_stub = True
        def generate(self, **kw): raise AssertionError("must not be called")
    assert obs.resolve_observation(_cache(), provider=S())["observation"] == ""


def test_failure_degrades_quietly():
    class Boom:
        is_stub = False
        def generate(self, **kw): raise RuntimeError("down")
    assert obs.resolve_observation(_cache(), provider=Boom())["observation"] == ""
