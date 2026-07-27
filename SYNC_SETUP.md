# Cross-device sync (git)

Paris keeps all its state as JSON files in one data directory. This makes the app sync
across your own machines by turning that directory into a private git repo. Sync is
**off until you set it up** and turns on automatically once the data dir is a git repo
with a remote. Secrets never sync — API keys live only in your OS keychain.

**Golden rule (single writer):** use the app on one machine at a time. Open → it pulls;
close → it commits and pushes. The app also pushes every ~10 minutes while open. Don't run
it on two machines simultaneously; if you do, git will flag a conflict rather than lose data.

## One-time setup

1. **Create a private repo** on GitHub (e.g. `paris-data`). Empty, no README.

2. **Add an SSH key per device** (SSH avoids interactive password prompts; HTTPS is flaky
   for automated push):
   ```
   ssh-keygen -t ed25519 -C "paris-<devicename>"
   # add the printed ~/.ssh/id_ed25519.pub to GitHub -> Settings -> SSH keys
   ```
   Make sure the agent holds it: `eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519`
   (add that to your shell profile so pushes never prompt).

3. **Turn the data dir into the repo — on your first/primary device only:**
   ```
   cd "<DATA_DIR>"            # see paths below
   git init
   git remote add origin git@github.com:<you>/paris-data.git
   git add -A
   git commit -m "paris: baseline"
   git branch -M main
   git push -u origin main
   ```
   The app writes a `.gitignore` and `.gitattributes` for you on first launch if they're
   missing, but you can commit the baseline before that — the ignored paths just won't be
   tracked.

4. **On every other device**, point the app at a clone of that repo instead of the
   default data dir, using the `PARIS_DATA_DIR` environment variable:
   ```
   git clone git@github.com:<you>/paris-data.git "<somewhere>/paris-data"
   ```
   - macOS/Linux: `export PARIS_DATA_DIR="<somewhere>/paris-data"` (put it in your shell
     profile, or in a small launch script).
   - Windows: set a user environment variable `PARIS_DATA_DIR` = `C:\...\paris-data`.

That's it. Launch normally (`python run_local.py` or the packaged app). You'll see
`[sync] git sync active (pulled latest)` in the console.

### Default data-dir locations
- **macOS / Linux:** `~/.paris_outreach`
- **Windows:** `%APPDATA%\ParisOutreach`
- **Override (any OS):** whatever you set in `PARIS_DATA_DIR`

## What syncs, what doesn't

Tracked (durable state): `drafts / queue / archive / follow_ups / sent_items /
suppressions / snippets.json`, `settings.json`, `voices/`, `voice_history/`, `batches/`,
`audit/`, `edit_ledger/`.

Ignored (regenerable or device-local): `caches/` (research cache), `outbox/` (staged
.eml), `session_stats.json`, `faults.log`, the lock file — and **`attachments/`, which is
kept local to each device by design.** If you want a CV/attachment on another machine,
copy it there once; the app references attachments by managed filename, so the drafts that
use them still sync.

## If you see a conflict

You edited on two devices without syncing in between. The app stays usable on your local
data and writes `SYNC_CONFLICT.md` with the exact commands to reconcile. Nothing is lost —
the other device's commits are safe on the remote. Auto-push pauses until you delete that
file.

## Turn it off

Set `PARIS_SYNC=0` to disable sync for a launch, or just don't configure a remote.
