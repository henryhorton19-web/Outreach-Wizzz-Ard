# Frontend fixes — tappability, consistency, cross-platform

Reported: buttons hard/impossible to tap, inconsistent behaviour, hard to navigate;
must work on both Windows and macOS.

## How this was diagnosed

The whole front end was run headlessly (jsdom) against the real served HTML with `fetch`
pointed at a live `PARIS_PROVIDER=stub` server, exercising: boot, every top-bar button,
all five tabs, the draft drawer (open + Save/Restore/Compare/Insert-snippet + voice &
attachment selects + body edit), and every modal including the Voice Editor.

**Result: zero JavaScript runtime errors on every path, and every handler attaches.** The
event wiring and app logic are sound. That relocated the reported symptoms to layout /
rendering — the one class of defect a non-rendering test can't see, and the class that
behaves exactly as described (width-dependent, so "inconsistent"; buttons present in the
DOM but not reachable, so "can't tap"). The two primary bugs both bite hardest under
**Windows display scaling (125–150%)**, which shrinks the packaged 1240px window's *CSS*
viewport to roughly 825–990px.

## Fixes (all in `ui/styles.css`; no logic changed)

1. **Approve / delete disappeared on narrow or scaled displays — primary bug.**
   The row actions live only in `.cell-act`, but the `@media (max-width: 880px)` rule set
   `.cell-act { display:none }`. Below 880px effective width (i.e. the default window at
   ~140% Windows scaling) a draft had **no Approve and no delete button at all**. Now the
   narrow layout drops only the *contact* column and keeps the actions column
   (`grid-template-columns: 22px minmax(0,1fr) auto auto`); the attachment chip is hidden
   there to avoid crowding.

2. **Top-bar utilities pushed off the right edge.**
   `.topbar` was a single non-wrapping flex row with no shrink/scroll protection, so as the
   viewport narrowed the right-hand icons (Sent, Voices, Settings, Guide) were shoved past
   the edge. Now brand, provider pill and the utility cluster are pinned (`flex: 0 0 auto`),
   the **tab strip** is the flexible element and scrolls internally if needed
   (`min-width:0; flex:0 1 auto; overflow-x:auto`), and tab labels collapse to icons at
   ≤1200px (reclaiming ~380px). The utilities can no longer be displaced.

3. **Split view stacks on very narrow / heavily scaled windows** (`≤720px`) so neither the
   queue nor the drafts column is crushed to a sliver.

4. **Two undefined CSS variables** (`--bg2`, `--accent`) were silently dropping the
   background/border on the drawer's "Earned Observation" block — aliased to existing tokens.

5. **`-webkit-backdrop-filter`** added for the footer (WKWebView / older Safari on macOS).

6. Secondary icon buttons enlarged 26→28px for easier tapping.

## Verification

- Stylesheet braces balance (448/448) and nesting stays at the expected depth — no rule was
  left open by the edits. The `@container` query for the drawer has a `@media (max-width:1000px)`
  fallback, so engines without container-query support still stack correctly.
- Full interaction harness re-run after the patch (boot → all 5 tabs → draft drawer + actions):
  still **zero JS runtime errors**, 19/19 top-bar buttons wired.
- `pytest`: **149 passed** (backend untouched; `index.html` and `app.js` are byte-for-byte
  identical to the original — every change is confined to `ui/styles.css`).

## What was *not* broken (checked, no change needed)

- No polling/`setInterval` re-renders the draft list, so a tap is never swallowed by a
  background rebuild. Every re-render is user-triggered, and each re-attaches its handlers.
- Row action buttons all `stopPropagation`, so tapping Approve/Retry/delete never also
  toggles the row's drawer.
- No live overlay intercepts clicks: modal scrims and the toast are correctly gated
  (`hidden` / `pointer-events:none`), and the fixed footer stays `display:none` throughout.
- Fonts are system-only with sane fallbacks on both OSes (Georgia / system-ui / Segoe UI on
  Windows; Georgia / -apple-system on macOS) — nothing to bundle.
- In the packaged window at its default (1240px) or minimum (940px) size, all controls fit
  and are reachable; the layout fixes above harden the browser-launch and DPI-scaled paths
  where the effective CSS width can drop below the old 880px break.

---

## Update 2 — modal reveal bug + cross-device sync

### Modal reveal bug (the "tap Voices → nothing, then tap Settings → both open")
**Root cause.** Modals are a pure `display:none ↔ flex` toggle. The four button-driven
openers (`openSettings`, `openVoicesManager`, `openArchive`, `openSuppressions`) are `async`
and removed the `hidden` class at the *end* of the function, *after* awaited network calls —
i.e. in a post-`await` microtask detached from the click gesture. WKWebView / WebView2 don't
composite a display flip made in a detached microtask until the next input event. So the first
tap set the state but never painted; the next tap (Settings) flushed the pending paint (Voices
appeared) *and* opened Settings — hence "both open". The Guide modal was unaffected because it
reveals synchronously inside its click handler.

**Fix.** Added a `showModal(id)` helper and moved the reveal to the **first line** of each of
the four openers, so it runs in the click's own task (guaranteed paint) with content loading
after. `openVoiceEditor` is synchronous and opens over an already-visible modal, so it was left
as-is. A static guard (see harness) asserts every opener calls `showModal` before its first
`await` to prevent regressions. Note: jsdom has no compositor, so this class of bug is invisible
to the automated harness — it was validated structurally (state + ordering); confirm visually by
opening Voices, Archive, and Suppressions each as the first action on a fresh launch.

### Cross-device sync (git, GitHub over SSH, attachments kept device-local)
- **Atomic writes.** `settings.atomic_write_text` (temp file + fsync + `os.replace`) now backs
  both `store.safe_write_text` and `settings.save_settings`, so a crash or a concurrent commit
  never captures a half-written JSON file.
- **`app/sync.py`.** Fail-open git wrapper: `pull --rebase --autostash` on start, commit+push on
  exit and every ~10 min, a local single-writer lock, and conflict surfacing (never auto-merge).
  Turns on automatically when the data dir is a git repo with a remote; `PARIS_SYNC=0` disables.
- **Launcher hooks.** `run_local.py` and `main.py` call `sync.on_start()` / `sync.on_exit()`.
- **Seed-collision fix (found via real two-device testing).** The app seeds `voices/*.json` on
  import, before sync runs; on a device's *first* pull these untracked seeds collided with the
  copies the first device had committed, aborting the pull. `pull()` now clears exactly those
  incoming untracked files first (regenerable seed data, remote wins) — never touching tracked
  changes or device-unique untracked files.
- See **SYNC_SETUP.md** for the one-time setup.
