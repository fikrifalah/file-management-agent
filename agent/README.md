# Drop-In File Management & Coding Agent

Drop this `agent/` folder into the root of **any** codebase to manage files, search, and edit code in the parent directory.

## Quick Start

**Windows (PowerShell / CMD):**
```powershell
.\agent\run.bat
```

**macOS / Linux:**
```bash
chmod +x ./agent/run.sh
./agent/run.sh
```

On first run, the launcher automatically:
1. Creates an isolated virtual environment inside `agent/.venv` and installs [requirements.txt](requirements.txt).
2. Prompts for your `GROQ_API_KEY` (if `agent/.env` doesn't exist yet) and saves it to `agent/.env`.
3. Starts the interactive agent targeting the parent project folder (`..`).

## Safety Boundaries
* Can only read/write inside the parent project root.
* Automatically blocks access to `agent/`, `.env`, `.git`, `.venv`, `node_modules`, and `__pycache__`.
