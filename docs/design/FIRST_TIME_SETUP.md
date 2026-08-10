# First-time setup (new device or fresh clone)

This is a **public code repo**. Your personal profile and real outreach voices are **not**
committed — they live in git-ignored local files so nothing personal is ever pushed here.

## 1. Clone Repositories (Code + Private Data)

To set up a new machine (laptop, Mac, desktop), clone both the public code repository and your private runtime data repository (`paris-data`) into their canonical locations:

### On macOS / Linux:
```bash
# 1. Clone the public code repo
git clone git@github.com:<you>/paris-outreach.git ~/paris-outreach
cd ~/paris-outreach

# 2. Clone your private data repo into the OS default location (~/.paris_outreach)
git clone git@github.com:<you>/paris-data.git ~/.paris_outreach

# 3. Set up Python virtual environment & install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### On Windows (PowerShell):
```powershell
# 1. Clone the public code repo
git clone git@github.com:<you>/paris-outreach.git "C:\Projects\paris-outreach"
cd "C:\Projects\paris-outreach"

# 2. Clone your private data repo into the OS default location (%APPDATA%\ParisOutreach)
git clone git@github.com:<you>/paris-data.git "$env:APPDATA\ParisOutreach"

# 3. Set up Python virtual environment & install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2. Add Your Private Profile (Once per device)
```bash
cp engine/config_local.example.py engine/config_local.py
# edit engine/config_local.py — fill in your name, contacts, CV facts
```
If `engine/config_local.py` is absent the app still runs, using a placeholder profile. `config_local.py` is git-ignored and will never be committed.

## 3. (Optional) private outreach voices
Put any private/real seed voices in `app/seed_voices_local/*.json` (git-ignored). They seed
on first launch alongside the generic starters in `app/seed_voices/`.

## 4. Run (System-Agnostic Synced Launcher)

Always use the system-agnostic launcher scripts to run the desktop application (`pywebview`). They automatically sync both public code and private data — `git pull --rebase --autostash` before launch, then on exit commit any changes and push whenever this machine has commits the remote doesn't (including commits you made by hand outside the app):

### Windows (PowerShell)
```powershell
.\run-paris.ps1
```

### macOS / Linux (Bash/Zsh)
```bash
./run-paris.sh
```

*(To run web-only offline demo without sync: `PARIS_PROVIDER=stub python run_local.py`)*
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
