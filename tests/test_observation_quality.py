"""The observation must vary in shape and must not diagnose the reader.

Measured on three real runs: two of three opened "Given [COMPANY]'s rapid
expansion, a [key|critical] operational challenge will [likely] be...", averaging
29 words. The schema offers mood=question and nothing used it.
"""
import app.observation_sampler as osamp


class _Stub:
    is_stub = False
    def __init__(self, payload): self.payload = payload; self.calls = 0
    def generate(self, **kw):
        self.calls += 1
        class R: text = self.payload
        return R()


_THREE = ('{"candidates":['
          '{"read":"Given their rapid expansion, a key operational challenge will be localisation","mood":"hypothesis","p":0.5},'
          '{"read":"how they keep one integration story while every market has its own rails","mood":"question","p":0.3},'
          '{"read":"licensed institution and five-minute integration are unusual together","mood":"question","p":0.2}]}')


def test_one_call_returns_candidates():
    p = _Stub(_THREE)
    out = osamp.sample_observations("sys", "user", provider=p, k=3)
    assert len(out) == 3 and p.calls == 1


def test_the_templated_candidate_loses():
    """"Given X, a key challenge will be" is the collapsed mode and must not win
    merely for having the highest probability."""
    out = osamp.sample_observations("s", "u", provider=_Stub(_THREE), k=3)
    chosen = osamp.select_observation(out)
    assert not chosen["read"].lower().startswith("given"), \
        f"the collapsed candidate was selected: {chosen}"


def test_a_question_mood_is_preferred_over_a_hypothesis():
    """A question is deferential by construction and cannot be wrong the way a
    diagnosis can. The voice's own style notes forbid a know-it-all tone."""
    out = osamp.sample_observations("s", "u", provider=_Stub(_THREE), k=3)
    assert osamp.select_observation(out)["mood"] == "question"


def test_an_overlong_candidate_is_penalised():
    long_read = " ".join(["word"] * 45)
    cands = [{"read": long_read, "mood": "question", "p": 0.9},
             {"read": "a short specific tension about their rails", "mood": "question", "p": 0.1}]
    assert osamp.select_observation(cands)["read"] != long_read


def test_provider_failure_returns_empty():
    class Boom:
        is_stub = False
        def generate(self, **kw): raise RuntimeError("down")
    assert osamp.sample_observations("s", "u", provider=Boom(), k=3) == []
