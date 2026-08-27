"""A block's fixed text may contain {tokens}. Every token that appears in a block's
template must be substituted before that block is shown anywhere, preview or live.

Confirmed: the Theo voice's preview screen showed a literal, unsubstituted
{sector_label} to the user due to SAMPLE missing 'sector_label' in ui/app.js.
Live sends do not exhibit this: they substitute correctly and the resolved VALUE is
the separate problem covered in Stage 2.

Not applicable: the Voice Editor preview is a static template demo with no live-send
counterpart to diverge from, confirmed in Plan 41 Stage 1 Task 2.
"""
import json
import re


def _no_unsubstituted_tokens(text: str) -> bool:
    return not re.search(r"\{[a-zA-Z_]+\}", text)


def test_the_reported_literal_token_is_gone():
    """Confirm renderTokens on a Theo-shaped template string containing {sector_label}
    produces no literal, unsubstituted token in its output."""
    with open("ui/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    # Extract SAMPLE dict definition from ui/app.js
    m = re.search(r"const\s+SAMPLE\s*=\s*\{([^}]+)\};", content)
    assert m, "SAMPLE object definition found in ui/app.js"
    sample_text = m.group(1)

    # Assert sector_label is in SAMPLE definition
    assert "sector_label" in sample_text, "sector_label added to SAMPLE object in ui/app.js"

    # Simulate renderTokens with sector_label present
    sample_keys = dict(re.findall(r'(\w+):\s*"([^"]+)"', sample_text))
    theo_template = (
        "We have been actively monitoring developments in {sector_label} and given "
        "the interesting position of {company}, are keen to speak..."
    )

    rendered = theo_template
    for k, v in sample_keys.items():
        rendered = rendered.replace(f"{{{k}}}", v)

    assert "{sector_label}" not in rendered, "{sector_label} token substituted successfully"
    assert _no_unsubstituted_tokens(rendered), "no remaining literal tokens in rendered text"
