"""Cost metrics as unit economics rather than a running total.

A total falls when you draft less, which looks like an improvement and is not
one. Cost per draft only falls when a draft genuinely gets cheaper.

The arithmetic lives server-side in a pure function so it can be tested and so
the UI does not carry the edge cases.
"""
from app.cost_metrics import compute_metrics


def test_average_is_total_over_drafts():
    m = compute_metrics({"cost": 1.10, "drafts": 60}, approved=0)
    assert round(m["per_draft"], 6) == round(1.10 / 60, 6)
    assert m["cost"] == 1.10
    assert m["drafts"] == 60


def test_zero_drafts_does_not_divide_by_zero():
    m = compute_metrics({"cost": 0.0, "drafts": 0}, approved=0)
    assert m["per_draft"] == 0.0
    assert m["per_approved"] == 0.0


def test_cost_before_any_draft_completes_is_reported_not_hidden():
    """Research can spend before a draft exists. That is real money and the
    meter must not report zero for it."""
    m = compute_metrics({"cost": 0.42, "drafts": 0}, approved=0)
    assert m["cost"] == 0.42
    assert m["per_draft"] == 0.0
    assert m["has_unattributed_spend"] is True


def test_per_approved_uses_approved_count():
    """The sharper number: it counts only drafts good enough to send, so it
    captures the waste that per-draft hides."""
    m = compute_metrics({"cost": 1.00, "drafts": 20}, approved=5)
    assert round(m["per_draft"], 4) == 0.05
    assert round(m["per_approved"], 4) == 0.20


def test_per_approved_is_zero_when_nothing_approved_yet():
    m = compute_metrics({"cost": 1.00, "drafts": 20}, approved=0)
    assert m["per_approved"] == 0.0


def test_token_split_passes_through():
    m = compute_metrics({"cost": 1.0, "drafts": 2, "in": 100, "out": 50, "cached": 25}, approved=1)
    assert (m["in"], m["out"], m["cached"]) == (100, 50, 25)
