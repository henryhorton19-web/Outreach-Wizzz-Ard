# First-time setup (new device or fresh clone)

This is a **public code repo**. Your personal profile and real outreach voices are **not**
committed — they live in git-ignored local files so nothing personal is ever pushed here.

## 1. Clone and install
```
git clone git@github.com:<you>/paris-outreach.git
cd paris-outreach
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Add your private profile (once per device)
```
cp engine/config_local.example.py engine/config_local.py
# edit engine/config_local.py — fill in your name, contacts, CV facts
```
If `engine/config_local.py` is absent the app still runs, using a placeholder profile.
`config_local.py` is git-ignored and will never be committed.

## 3. (Optional) private outreach voices
Put any private/real seed voices in `app/seed_voices_local/*.json` (git-ignored). They seed
on first launch alongside the generic starters in `app/seed_voices/`.

## 4. Run
```
python run_local.py            # opens in your browser
# PARIS_PROVIDER=stub python run_local.py   # offline demo, no API calls
```
API keys are prompted on first use and stored in your OS keychain — never on disk, never here.

---

## Two-repo model (why nothing personal is here)

| Repo | Visibility | Holds | Syncs via |
|------|-----------|-------|-----------|
| **paris-outreach** (this) | public | source code only | `git` (you push/pull across devices) |
| **paris-data** | private | drafts, queue, real voices, settings | the app's built-in sync (see `SYNC_SETUP.md`) |

Your runtime data (drafts/voices/etc.) is synced automatically by the app across your own
machines via the separate private `paris-data` repo — see `SYNC_SETUP.md`. This code repo and
that data repo are deliberately kept apart.
