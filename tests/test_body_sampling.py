"""Ask for a distribution of bodies, not one body."""
import app.body_sampler as bs


class _Stub:
    is_stub = False
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate(self, **kw):
        self.calls += 1
        class R: text = self.payload
        return R()


_FOUR = ('{"candidates":['
         '{"body":"Having judged companies from the investor side and also built things, X stood out.","p":0.5},'
         '{"body":"Holding a no-code promise while agents multiply is the hard part of this.","p":0.25},'
         '{"body":"Your conversion numbers are unusual for a team that size.","p":0.15},'
         '{"body":"I have built into that exact constraint before.","p":0.10}]}')


def test_one_call_returns_k_candidates():
    p = _Stub(_FOUR)
    out = bs.sample_bodies("sys", "user", provider=p, k=4)
    assert len(out) == 4 and p.calls == 1, "must be one call, not k calls"


def test_candidates_carry_probabilities():
    assert all("p" in c for c in bs.sample_bodies("s", "u", provider=_Stub(_FOUR), k=4))


def test_provider_failure_returns_empty():
    class Boom:
        is_stub = False
        def generate(self, **kw): raise RuntimeError("down")
    assert bs.sample_bodies("s", "u", provider=Boom(), k=4) == []
