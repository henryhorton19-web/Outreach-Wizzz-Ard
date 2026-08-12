"""Sourcing must perform a real search and must not invent companies.

Three defects made this impossible: live search was gated on an environment
variable nothing sets, the live path imported a module that does not exist, and
the query was identical every run so the seen ledger discarded everything.
"""
from app.sourcing.harvest.grounded_search import GroundedSearchHarvester


class FakeProvider:
    """A provider that reports having searched."""
    is_stub = False
    provider = "gemini"

    def __init__(self, payload: str, searches: int = 3):
        self.payload = payload
        self.searches = searches
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        payload, searches = self.payload, self.searches

        class Result:
            text = payload
            searches_used = searches
            source_urls = ["https://example.com/a"]
        return Result()


THREE_ROWS = """[
 {"name":"Alpha Systems","city":"Berlin","country":"Germany","press_signal":"raised a round",
  "employees_band":"51-200","website":"https://alpha.example","source_url":"https://news.example/a"},
 {"name":"Beta Health","city":"Paris","country":"France","press_signal":"entered a new market",
  "employees_band":"11-50","website":"","source_url":"https://news.example/b"},
 {"name":"Ghost Corp","city":"","country":"","press_signal":"","employees_band":"",
  "website":"","source_url":""}
]"""


def test_a_real_provider_triggers_a_live_search():
    """No environment variable should be required."""
    provider = FakeProvider(THREE_ROWS)
    GroundedSearchHarvester().harvest(max_items=40, provider=provider)
    assert provider.calls == 1, "the live path did not run for a real provider"


def test_web_search_is_enabled_on_the_call():
    provider = FakeProvider(THREE_ROWS)
    GroundedSearchHarvester().harvest(max_items=40, provider=provider)
    assert provider.last_kwargs.get("use_web") is True


def test_rows_without_a_citation_are_dropped():
    """Ghost Corp has no source_url, so it was recalled or invented."""
    rows = GroundedSearchHarvester().harvest(max_items=40, provider=FakeProvider(THREE_ROWS))
    names = {r["name"] for r in rows}
    assert "Alpha Systems" in names
    assert "Ghost Corp" not in names, "an uncited company was kept"


def test_a_missing_website_is_left_blank():
    """A guessed domain flows into contact discovery and then into a real email."""
    rows = GroundedSearchHarvester().harvest(max_items=40, provider=FakeProvider(THREE_ROWS))
    beta = next(r for r in rows if r["name"] == "Beta Health")
    assert beta["meta"]["website"] == ""
    assert "beta" not in beta["meta"]["website"]


def test_a_batch_with_no_searches_is_flagged_ungrounded():
    """The model can answer a broad question from training data without searching."""
    rows = GroundedSearchHarvester().harvest(
        max_items=40, provider=FakeProvider(THREE_ROWS, searches=0))
    assert rows, "expected rows to be returned"
    assert all(r["meta"]["grounded"] is False for r in rows)


def test_the_query_changes_between_runs():
    """Identical queries return identical companies, which the seen ledger then
    discards, which is why later runs found nothing."""
    h = GroundedSearchHarvester()
    seen: list[str] = []
    queries = []
    for run in range(4):
        window = h.window_for_run(180, run)
        queries.append(h.build_query(None, window, seen, ""))
        seen = seen + [f"company_{run}"]
    assert len(set(queries)) >= 3, f"only {len(set(queries))} distinct queries across 4 runs"


def test_seen_names_appear_in_the_query_as_exclusions():
    q = GroundedSearchHarvester().build_query(None, 180, ["Alpha Systems", "Beta Health"], "")
    assert "Alpha Systems" in q and "Beta Health" in q


def test_a_stub_provider_still_uses_fixtures():
    class Stub:
        is_stub = True
        provider = "stub"
        def generate(self, **kwargs):
            raise AssertionError("a stub provider must not be called")
    rows = GroundedSearchHarvester().harvest(max_items=40, provider=Stub())
    assert rows, "the offline path should still return sample rows"
