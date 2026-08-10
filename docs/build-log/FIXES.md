> Historical build note ? kept for provenance, not maintained.

# Fixes applied

Two issues were addressed: the drawer layout bug from `error_report.md`, and a review +
repair of the Apollo verification path.

## 1. Drawer layout — `.letter-wrap` squished (`ui/styles.css`)

**Real cause (verified by rendering the drawer headless and measuring the box tree, not the
`.letter-head-row` legacy rule the report suspected — those rules are inert, no matching DOM):**

- `.drawer-grid` used `grid-template-columns: minmax(260px, 340px) 1fr`. When space got tight the
  browser shrank the flexible `1fr` track first, and because `.letter-wrap` has `min-width: 0`
  that track collapsed to a sliver (measured 648px → 149px) while `.research` stayed pinned at its
  340px maximum. Result: research "renders fine", letter-wrap crushed.
- The stack-to-one-column breakpoint was gated on **viewport** width (`@media max-width: 900px`),
  but the drawer lives inside the narrower right column of the `280px 1fr` split, so a wide window
  could still leave the drawer too narrow for two columns — the 901–1050px band was broken.
- `.drafts-col` (the outer `1fr`) was missing the `min-width: 0` its `.queue-col` sibling has, and
  `.c-to` had no wrap rule, so long recipient emails could overflow the letter card.

**Changes:**
- Research track → `minmax(240px, 300px)`; letter track → `minmax(0, 1fr)` (can't be starved).
- Stack based on the **drawer's own width** via a container query
  (`.drawer-inner { container-type: inline-size }` + `@container (max-width: 640px)`), with a
  `@media (max-width: 1000px)` fallback for engines without container-query support.
- Added `min-width: 0` to `.drafts-col`; let `.c-to` / `.letter-head` wrap.

Verified: no squish and zero overflow from 901px to 1440px; clean two-column above ~1024px,
graceful full-width stack below it.

## 2. Apollo verification — "bad api call" (`app/apollo.py`, `ui/app.js`)

Endpoint (`/api/v1/people/bulk_match`) and the `x-api-key` header were already correct
(checked against Apollo's current docs). The real defects:

- **Emails never returned.** `reveal_personal_emails` is a *query* parameter and was never sent, so
  Apollo replied `200` with no `email` on any match and verification silently did nothing. Now sent
  as a query param (toggle with `PARIS_APOLLO_REVEAL_EMAILS=0`). Phone reveal is intentionally left
  off — Apollo requires a `webhook_url` for phones, which this desktop app has no endpoint for.
- **The "bad api call".** Any HTTP error (401 bad key, 422, 429, …) was swallowed and printed as a
  cryptic `HTTP Error 400`-style line, then verification silently degraded. Added a typed
  `ApolloError` that reads Apollo's JSON error body and produces an actionable message
  (e.g. `Apollo API 401: invalid api key (check the Apollo API key in Settings)`), which is now
  surfaced in the approval receipt's `note`/`api_error`. Staging the draft still proceeds regardless.
- **UI never showed the result.** `ui/app.js` read `r.send.receipts[0]` (an old response shape); the
  server returns `r.apollo`. Rewired `approveOne` to read `r.apollo` and toast the outcome
  (opened count, or the failure/verification reason as a warning).
- Defensive: replaced the `[{}] * n` shared-reference list with per-element dicts, and guarded
  `decide()` against a `null` match (Apollo returns `null` for unmatched people).

All 21 existing tests pass; the app boots under `PARIS_PROVIDER=stub` and serves the UI.
