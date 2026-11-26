# Drop-In File Management & Coding Agent (`smolagents` + Groq)

A lightweight, self-contained AI file management and coding agent powered by **`smolagents` (`CodeAgent`)** and **Groq**. Drop the `agent/` folder into the root of **any** codebase to inspect, organize, search, create, and edit files in the parent directory.

---

## What This Tool Is Good For

1. **Project Scaffolding & Boilerplate Generation**
   * Creating multi-file project structures (e.g., Flask/FastAPI backends, HTML/CSS/JS frontends, config files) in a single step.
   * Because it uses `CodeAgent`, it can batch directory creation, multiple `create_file` calls, and `final_answer` into one Python action step.
2. **File & Folder Organization**
   * Listing directories, renaming files, moving files into structured subfolders, and cleaning up unused files/directories.
3. **Codebase Search & Targeted Refactoring**
   * Searching across all project text files with `search_in_files` (grep-style `file:line: match`).
   * Reading specific line ranges with `read_file` without blowing up the LLM context window.
   * Making surgical edits to existing code using `replace_in_file` without rewriting entire files.
4. **Zero-Config Drop-In Usage**
   * Uses `Path(__file__).resolve().parents[1]` to automatically target the parent folder (`BASE_DIR`) while isolating its own virtual environment (`agent/.venv`) and secrets (`agent/.env`).

---

## Security & Sandboxing Architecture

All 8 tools in [tools.py](file:///c:/Users/fikrifalah/Documents/GitHub/file-management-agent/agent/tools.py) enforce path validation via `_safe_path()` before touching the filesystem:

* **Root Prison (`BASE_DIR`)**: Every path is resolved via `.resolve()` and checked with `.is_relative_to(BASE_DIR)`. Path traversal (`..`, `../../`, or external absolute paths) is blocked.
* **Self-Protection (`AGENT_DIR`)**: The agent cannot list, read, modify, move, or delete anything inside `agent/` (nor can it delete or move `.` or any ancestor directory containing `agent/`).
* **Protected Secrets & Heavy Folders (`BLOCKED_NAMES`)**: Automatically blocks and hides `.env` (including `.env.*` variants), `.git`, `.venv`, `venv`, `node_modules`, and `__pycache__`.
* **Binary & File Size Guards**: `read_file`, `search_in_files`, and `replace_in_file` reject binary files (null-byte detection) and files over `5 MB` (`MAX_TEXT_FILE_BYTES`) to prevent memory exhaustion or context overflow.
* **Overwrite & Delete Safeguards**:
  * `create_file` refuses to overwrite an existing file unless `overwrite=True` is explicitly passed.
  * `replace_in_file` requires `old_text` to match **uniquely** (exact count of `1`) so ambiguous partial matches never corrupt code.
  * `delete_file` refuses to delete non-empty directories unless `recursive=True` is explicitly set.

---

## Limitations

1. **No Shell / Terminal Execution Tool**
   * The agent can read, write, move, and delete files, but it **cannot run shell commands**, start servers, install `pip`/`npm` packages, or execute your test suite automatically. You run generated code yourself in your terminal.
2. **Text Files Only (`<= 5 MB`)**
   * `read_file`, `search_in_files`, and `replace_in_file` only operate on UTF-8 text files under 5 MB. It cannot inspect images, PDFs, audio, or binary databases.
3. **Stateless Across Separate Prompts**
   * Each prompt in the interactive loop runs a fresh `agent.run(task)` execution (up to `max_steps=10`). If a follow-up request depends on files created earlier, the agent will inspect the directory/files again rather than relying on prior chat memory.
4. **Permanent Deletions**
   * `delete_file` permanently removes files/folders (`unlink` / `rmtree`) rather than moving them to the OS Recycle Bin. Always use Git in your host repository so changes can be reverted with `git checkout` / `git clean`.
5. **Groq API Rate Limits & Model Availability**
   * Depends on an active internet connection and the models enabled for your `GROQ_API_KEY`. While [main.py](file:///c:/Users/fikrifalah/Documents/GitHub/file-management-agent/agent/main.py) automatically rotates across available Groq models on errors/rate limits, free-tier accounts may hit daily token caps (`TPD`) or requests-per-minute (`RPM`) limits on large tasks.

---

## How to Run It

### Option 1: Sleek Desktop App (`pywebview` + HTML/Tailwind UI)
Build and launch the standalone native desktop application ([desktop_app.py](file:///c:/Users/fikrifalah/Documents/GitHub/file-management-agent/agent/desktop_app.py) + [ui/index.html](file:///c:/Users/fikrifalah/Documents/GitHub/file-management-agent/agent/ui/index.html)):

```powershell
.\agent\.venv\Scripts\python.exe agent\build.py
```

This creates **`dist/FileManagementAgent/FileManagementAgent.exe`**.
* **To share with users:** Zip the entire **`dist/FileManagementAgent/`** folder. Users unzip it anywhere and double-click **`FileManagementAgent.exe`** (no Python or terminal required).
* **Groq API Key Settings:** On first launch, a **Settings** modal opens automatically where users input their `GROQ_API_KEY` (saved locally to `.env` next to the executable) and can choose their target workspace folder.

### Option 2: One-Command Terminal Launcher
Drop the `agent/` folder into any project and run:

**Windows (PowerShell / CMD):**
```powershell
.\agent\run.bat
```

**macOS / Linux:**
```bash
chmod +x ./agent/run.sh
./agent/run.sh
```

On first launch, the script automatically:
1. Creates `agent/.venv` and installs [requirements.txt](file:///c:/Users/fikrifalah/Documents/GitHub/file-management-agent/agent/requirements.txt).
2. Prompts for your `GROQ_API_KEY` (if `agent/.env` is not set) and saves it to `agent/.env`.
3. Starts the interactive CLI (`You>`).

### Option 2: One-Shot CLI Command
Pass a prompt directly as command-line arguments to run a single task and exit:
```powershell
.\agent\run.bat "create a docs folder with an architecture.md template"
```

### Option 3: Manual Virtual Environment Setup
```powershell
cd agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

---

## Running the Automated Security & Tool Tests
To verify path sandboxing, secret hiding, and all 8 tools independently (without using Groq API tokens):
```powershell
.\agent\.venv\Scripts\python.exe agent\test_tools.py
```
