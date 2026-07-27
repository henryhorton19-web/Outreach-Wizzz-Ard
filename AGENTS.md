# Paris Outreach Project Rules for AI Assistants (`AGENTS.md`)

When opening, modifying, or launching this repository in a fresh chat or workspace, follow these mandatory rules:

## 1. Canonical Launch Command (System-Agnostic & Synced)
When the user asks to "open Paris", "run Paris Outreach", "launch the app", or start the system, **always provide the system-agnostic synced launcher command** for their OS. Do **not** run `python main.py` or `python server.py` directly unless explicitly instructed to bypass synchronization.

### Windows (PowerShell)
```powershell
cd "<ABSOLUTE_PATH_TO_PARIS_OUTREACH>"
.\run-paris.ps1
```

### macOS / Linux (Bash/Zsh)
```bash
cd "<ABSOLUTE_PATH_TO_PARIS_OUTREACH>"
./run-paris.sh
```

## 2. Always Open Desktop App Mode
Paris Outreach is a self-contained desktop application powered by Python (`pywebview` + `FastAPI`). When launching the app, **always open the local desktop application GUI** (which is what `run-paris.ps1`, `run-paris.sh`, and `main.py` do). Do not start only the headless backend server (`server.py` or `uvicorn`) unless the user explicitly requests web-only mode.

## 3. Never Execute Launch Commands Directly
**Do NOT execute the application launch commands yourself using terminal tools.** Always output the exact copy-pasteable commands (with full absolute directory paths) in markdown code blocks so the user can run them interactively in their own terminal window.

## 4. Automatic Two-Repo Git Sync Architecture
The launcher scripts (`run-paris.ps1` and `run-paris.sh` -> `run_synced.py`) automatically manage a **Two-Repo Model**:
* **Public Code Repo (`paris-outreach`)**: Contains source code only. The launcher automatically executes `git pull` before starting the application, and upon app exit, checks for modified code files and automatically runs `git add -u`, `git commit -m "Auto-commit: Paris Outreach session updates"`, and `git push`.
* **Private Data Repo (`paris-data`)**: Holds runtime data (drafts, outbox queue, edit ledger, and real voices) stored in `%APPDATA%\ParisOutreach` (Windows) or `~/.paris_outreach` (macOS/Linux). The application runtime (`app/sync.py`) automatically pulls on launch, pushes every ~10 minutes, and commits/pushes upon closing.

## 5. Data Privacy & Local Overrides
Never commit or suggest committing personal profile information (`engine/config_local.py`) or private voice data (`app/seed_voices_local/`) to this repository. They are strictly git-ignored and must remain local to each user device.
