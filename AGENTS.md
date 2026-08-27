# Paris Outreach / Outreach Wizz-ard Project Rules & Setup Guide (`AGENTS.md`)

When opening, modifying, setup, or launching this repository in a fresh chat or workspace, follow these mandatory rules and setup instructions:

---

## 1. Fresh Environment Setup Guide ("Virgin Run")

Follow these steps to set up and run Outreach Wizz-ard on a brand new local machine or clean Python environment:

### Prerequisites
* **Python**: Version 3.11 to 3.13 (Python 3.14+ is unsupported due to third-party C-extension dependencies).
* **Git**: Installed and available on system `PATH`.
* **OS**: Windows 10/11, macOS, or Linux.

### Step 1: Clone Repository
```powershell
git clone git@github.com:henryhorton19-web/Outreach-Wizz-Ard.git "C:\Users\HenryHorton\OneDrive\Documents\Internship\09 Personal Projects\paris-outreach"
cd "C:\Users\HenryHorton\OneDrive\Documents\Internship\09 Personal Projects\paris-outreach"
```

### Step 2: Create Virtual Environment & Install Dependencies
```powershell
cd "C:\Users\HenryHorton\OneDrive\Documents\Internship\09 Personal Projects\paris-outreach"
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```
*(On macOS/Linux, activate using `source venv/bin/activate`)*

### Step 3: Launch Application
Run the system-agnostic launcher script. This script automatically handles pre-launch Git sync, bootstraps default starter voices and sourcing prompts, and launches the native PyWebView desktop GUI:

**Windows (PowerShell)**:
```powershell
cd "C:\Users\HenryHorton\OneDrive\Documents\Internship\09 Personal Projects\paris-outreach"
.\run-wizzard.ps1
```

**macOS / Linux (Bash/Zsh)**:
```bash
cd "C:\Users\HenryHorton\OneDrive\Documents\Internship\09 Personal Projects\paris-outreach"
./run-wizzard.sh
```

---

## 2. Mandatory AI Assistant Operating Rules

### Canonical Launch Command
When the user asks to "open Paris", "run Paris Outreach", "open Wizz-ard", "run Outreach Wizz-ard", "launch the app", or start the system, **always provide the system-agnostic synced launcher command** for their OS. Do **not** run `python main.py` or `python server.py` directly unless explicitly instructed to bypass synchronization.

### Always Open Desktop App Mode
Outreach Wizz-ard is a self-contained desktop application powered by Python (`pywebview` + `FastAPI`). When launching the app, **always open the local desktop application GUI** (which `run-wizzard.ps1`, `run-wizzard.sh`, and `main.py` do by default). Do not start only the headless backend server unless the user explicitly requests web-only mode.

### Never Execute Launch Commands Directly
**Do NOT execute the application launch commands yourself using tools.** Always output the exact copy-pasteable launcher commands (with full absolute directory paths) in markdown code blocks so the user can run them interactively in their terminal window.

---

## 3. Automatic Two-Repo Git Sync Architecture
The launcher scripts (`run-wizzard.ps1` and `run-wizzard.sh` -> `run_synced.py`) automatically manage a **Two-Repo Model**:
* **Public Code Repo (`paris-outreach`)**: Contains source code only. Before launch, the launcher runs `git pull --rebase --autostash`. On exit, it runs `git add -A` and commits if the working tree is dirty, then pushes whenever the branch is ahead of upstream. A failed push prints a warning rather than ignoring errors.
* **Private Data Repo (`paris-data`)**: Holds runtime data (drafts, outbox queue, edit ledger, and real voices) stored in `%APPDATA%\OutreachWizzard` (Windows) or `~/.outreach_wizzard` (macOS/Linux). The application runtime (`app/sync.py`) automatically pulls on launch, pushes periodically, and commits/pushes upon closing.

---

## 4. Data Privacy & Local Overrides
Never commit or suggest committing personal profile information (`engine/config_local.py`) or private voice data (`app/seed_voices_local/`) to this repository. They are strictly git-ignored and must remain local to each user device.
