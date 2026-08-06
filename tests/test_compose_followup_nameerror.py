"""Regression test: verify compose_voice follow-up instruction formatting does not raise NameError (+ sci)."""
import app.compose as compose
from app.models import CustomVoice, Block, Style, Evidence
from app.providers.stub import StubProvider

def test_compose_blocks_followup_no_nameerror():
    provider = StubProvider()
    voice = CustomVoice(
        id="test_v",
        display_name="Test Voice",
        blocks=[Block(id="body", mode="ai", text="", fact_scope=["target_proofs"])],
        style=Style(),
        evidence=Evidence(),
    )
    spec = {
        "send_to": "target@example.com",
        "evidence": [],
        "spine": "Test spine",
        "allowed_facts": [],
    }
    followup = {
        "step": 1,
        "original_subject": "Prior Subject",
        "original_body": "Prior Body",
    }
    # Should complete without raising NameError (+ sci)
    res = compose.compose_voice(provider, voice, voice.blocks, spec, tokens={}, shortlist=[], followup=followup)
    assert isinstance(res, dict)
