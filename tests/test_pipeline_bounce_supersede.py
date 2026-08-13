"""A bounced target that has been re-drafted or re-sent must leave the Bounced column.

pipeline.draft_retarget stages the retry under "{parent}__b{n}", so the board must group sent
history by ROOT slug or the original bounce can never be superseded.
"""
import pytest

from app import store, pipeline_view, settings as S
from app.models import CompanyState, State, SentItem, ReplyState


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SENT_ITEMS_FILE", tmp_path / "sent_items.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def _sent(sid, slug, state, at):
    return SentItem(id=sid, slug=slug, name="Acme", voice="v",
                    reply_state=state, approved_at=at)


def _cols(list_id=""):
    return pipeline_view.assemble(list_id=list_id)["columns"]


def _slugs(col):
    return [c["slug"] for c in col]


def test_bounce_with_staged_retry_shows_only_in_drafted():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.bounced, "2026-08-01T10:00:00+00:00"))
    store.upsert_draft(CompanyState(slug="acme__b1", name="Acme", state=State.drafted))
    cols = _cols()
    assert _slugs(cols["drafted"]) == ["acme__b1"]
    assert cols["bounced"] == []


def test_approved_retry_moves_the_target_to_sent():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.bounced, "2026-08-01T10:00:00+00:00"))
    store.upsert_sent_item(_sent("acme__b1#1", "acme__b1", ReplyState.awaiting,
                                 "2026-08-02T10:00:00+00:00"))
    cols = _cols()
    assert len(cols["sent"]) == 1
    assert cols["bounced"] == []


def test_exhausted_bounce_stays_in_bounced():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.bounced_exhausted,
                                 "2026-08-01T10:00:00+00:00"))
    store.upsert_draft(CompanyState(slug="acme__b1", name="Acme", state=State.drafted))
    cols = _cols()
    assert len(cols["bounced"]) == 1


def test_plain_bounce_with_no_retry_stays_in_bounced():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.bounced, "2026-08-01T10:00:00+00:00"))
    assert len(_cols()["bounced"]) == 1


def test_replied_send_is_not_affected_by_a_live_draft():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.replied, "2026-08-01T10:00:00+00:00"))
    store.upsert_draft(CompanyState(slug="acme__b1", name="Acme", state=State.drafted))
    cols = _cols()
    assert len(cols["replied"]) == 1


def test_one_target_counts_once_in_the_funnel():
    store.upsert_sent_item(_sent("acme#1", "acme", ReplyState.bounced, "2026-08-01T10:00:00+00:00"))
    store.upsert_sent_item(_sent("acme__b1#1", "acme__b1", ReplyState.awaiting,
                                 "2026-08-02T10:00:00+00:00"))
    assert pipeline_view.assemble()["summary"]["sent"] == 1
