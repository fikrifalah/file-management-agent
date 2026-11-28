import os
import shutil
import sys
import threading
from pathlib import Path
from smolagents import tool


def get_app_dir() -> Path:
    """Return directory containing the executable (when frozen) or this script (in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_resource_dir() -> Path:
    """Return directory containing bundled read-only resources (sys._MEIPASS when frozen)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent


AGENT_DIR = get_app_dir()


def _default_base_dir() -> Path:
    env_root = os.getenv("TARGET_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    if AGENT_DIR.parent.name.lower() == "dist" and AGENT_DIR.parent.parent.exists():
        return AGENT_DIR.parent.parent.resolve()
    return AGENT_DIR.parent.resolve()


BASE_DIR = _default_base_dir()
BLOCKED_NAMES = {
    ".env",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    "config.json",
    "rag_index.db",
    "rag_index.db-wal",
    "rag_index.db-shm",
    "models_cache",
}
MAX_TEXT_FILE_BYTES = 5 * 1024 * 1024  # 5 MB safety limit for text operations

_rag_engine = None
_rag_init_lock = threading.RLock()
_indexed_workspace: Path | None = None


def get_rag_engine():
    global _rag_engine
    if _rag_engine is None:
        with _rag_init_lock:
            if _rag_engine is None:
                from rag_engine import LocalRagEngine

                _rag_engine = LocalRagEngine(
                    db_path=AGENT_DIR / "rag_index.db",
                    cache_dir=AGENT_DIR / "models_cache",
                )
    return _rag_engine


def initialize_rag_index() -> dict[str, int]:
    """Embed the entire active BASE_DIR into SQLite + sqlite-vec so it is ready for RAG search."""
    global _indexed_workspace
    with _rag_init_lock:
        stats = get_rag_engine().index_directory(BASE_DIR, AGENT_DIR, _is_blocked_part)
        _indexed_workspace = BASE_DIR
        return stats


def _ensure_rag_ready() -> None:
    if _indexed_workspace != BASE_DIR:
        initialize_rag_index()


def _sync_file_to_rag(target: Path) -> None:
    """Embed a newly created or modified file into the RAG database if RAG is active."""
    try:
        if _rag_engine is not None or _indexed_workspace is not None:
            get_rag_engine().index_file(target, BASE_DIR)
    except Exception:
        pass


def _remove_path_from_rag(target: Path, is_dir: bool = False) -> None:
    try:
        if _rag_engine is not None or _indexed_workspace is not None:
            get_rag_engine().remove_path(target, BASE_DIR, is_dir=is_dir)
    except Exception:
        pass


def get_base_dir() -> Path:
    return BASE_DIR


def set_base_dir(new_base: str | Path) -> Path:
    """Allow the desktop UI to switch the active target workspace directory."""
    global BASE_DIR, _indexed_workspace
    resolved = Path(new_base).resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Invalid target directory: '{new_base}'")
    if resolved != BASE_DIR:
        BASE_DIR = resolved
        _indexed_workspace = None
    return BASE_DIR


def _is_blocked_part(part: str) -> bool:
    low = part.lower()
    return low in BLOCKED_NAMES or low.startswith(".env.") or low.startswith("rag_index.db")


def _safe_path(user_path: str) -> Path:
    if not user_path or not user_path.strip():
        raise ValueError("Path cannot be empty.")

    target = (BASE_DIR / user_path).resolve()
    if not target.is_relative_to(BASE_DIR):
        raise ValueError(f"Access denied: '{user_path}' is outside the project root.")

    rel_parts = target.relative_to(BASE_DIR).parts
    if target.is_relative_to(AGENT_DIR) or any(_is_blocked_part(p) for p in rel_parts):
        raise ValueError(f"Access denied: '{user_path}' is a protected path.")
    return target


def _is_binary_Or_too_large(target: Path) -> str | None:
    size = target.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        return f"Error: '{target.relative_to(BASE_DIR)}' is too large ({size} bytes; max {MAX_TEXT_FILE_BYTES})."
    with target.open("rb") as f:
        chunk = f.read(1024)
    if b"\x00" in chunk:
        return f"Error: '{target.relative_to(BASE_DIR)}' appears to be a binary file."
    return None


@tool
def rag_search(query: str) -> str:
    """Semantic RAG search across workspace files (PDF, DOCX, XLSX, text, and code).

    Args:
        query: Natural language search query to find relevant content across the directory.
    """
    try:
        _ensure_rag_ready()
        return get_rag_engine().search(query)
    except Exception as e:
        return f"Error: {e}"


@tool
def list_directories(path: str = ".") -> str:
    """List files and folders in a directory.

    Args:
        path: Relative directory path. Defaults to ".".
    """
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: '{path}' does not exist."
        if not target.is_dir():
            return f"Error: '{path}' is not a directory."

        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            resolved = item.resolve()
            if resolved == AGENT_DIR or _is_blocked_part(item.name):
                continue
            if item.is_dir():
                entries.append(f"[DIR]  {item.name}/")
            else:
                entries.append(f"[FILE] {item.name} ({item.stat().st_size}B)")

        return "\n".join(entries) if entries else "(empty directory)"
    except Exception as e:
        return f"Error: {e}"


@tool
def create_directory(path: str) -> str:
    """Create a directory and any missing parent folders.

    Args:
        path: Relative directory path.
    """
    try:
        target = _safe_path(path)
        if target.exists():
            return f"Directory '{path}' already exists." if target.is_dir() else f"Error: File exists at '{path}'."
        target.mkdir(parents=True, exist_ok=True)
        return f"Created directory '{target.relative_to(BASE_DIR)}'."
    except Exception as e:
        return f"Error: {e}"


@tool
def create_file(path: str, suffix: str = "", content: str = "", overwrite: bool = False) -> str:
    """Create a file (and parent folders automatically), write content, and embed it into the RAG database.

    Args:
        path: Relative file path.
        suffix: Optional extension (e.g. "py"). Defaults to "".
        content: Text content to write. Defaults to "".
        overwrite: Replace existing file if True. Defaults to False.
    """
    try:
        if suffix:
            norm_suffix = suffix if suffix.startswith(".") else f".{suffix}"
            if not path.endswith(norm_suffix):
                path = f"{path}{norm_suffix}"

        target = _safe_path(path)
        if target.exists() and target.is_dir():
            return f"Error: '{path}' is a directory."
        if target.exists() and not overwrite:
            return f"Error: '{path}' exists. Set overwrite=True to replace."

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _sync_file_to_rag(target)
        return f"Created file '{target.relative_to(BASE_DIR)}' and indexed into RAG."
    except Exception as e:
        return f"Error: {e}"


@tool
def move_file(source: str, destination: str) -> str:
    """Move or rename a file or directory (creates parent folders automatically).

    Args:
        source: Relative source path.
        destination: Relative destination path.
    """
    try:
        src = _safe_path(source)
        dst = _safe_path(destination)

        if not src.exists():
            return f"Error: Source '{source}' does not exist."
        if src == BASE_DIR or AGENT_DIR.is_relative_to(src):
            return "Error: Cannot move project root or a directory containing the agent."

        was_dir = src.is_dir()
        if dst.is_dir():
            dst = _safe_path(str((dst / src.name).relative_to(BASE_DIR)))

        if dst.exists():
            return f"Error: Destination '{destination}' already exists."

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

        _remove_path_from_rag(src, is_dir=was_dir)
        if dst.is_file():
            _sync_file_to_rag(dst)
        elif dst.is_dir():
            for f in _iter_safe_files(dst):
                _sync_file_to_rag(f)

        return f"Moved '{src.relative_to(BASE_DIR)}' to '{dst.relative_to(BASE_DIR)}'."
    except Exception as e:
        return f"Error: {e}"


@tool
def delete_file(path: str, recursive: bool = False) -> str:
    """Delete a file or directory.

    Args:
        path: Relative path to delete.
        recursive: Delete non-empty directory if True. Defaults to False.
    """
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: '{path}' does not exist."
        if target == BASE_DIR or AGENT_DIR.is_relative_to(target):
            return "Error: Cannot delete project root or a directory containing the agent."

        if target.is_dir():
            if any(target.iterdir()) and not recursive:
                return f"Error: '{path}' is not empty. Set recursive=True."
            shutil.rmtree(target) if recursive else target.rmdir()
            _remove_path_from_rag(target, is_dir=True)
            return f"Deleted directory '{path}'."

        target.unlink()
        _remove_path_from_rag(target, is_dir=False)
        return f"Deleted file '{path}'."
    except Exception as e:
        return f"Error: {e}"


@tool
def read_file(path: str, start_line: int = 1, end_line: int = 100) -> str:
    """Read lines from a text file with line numbers.

    Args:
        path: Relative file path.
        start_line: 1-based start line. Defaults to 1.
        end_line: 1-based end line. Defaults to 100.
    """
    try:
        target = _safe_path(path)
        if not target.exists():
            return f"Error: '{path}' does not exist."
        if not target.is_file():
            return f"Error: '{path}' is not a file."
        if start_line < 1 or end_line < start_line:
            return "Error: Invalid line range."

        check_err = _is_binary_Or_too_large(target)
        if check_err:
            return check_err

        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        if total == 0:
            return "(empty file)"

        slice_lines = lines[start_line - 1 : end_line]
        numbered = [f"{i}: {line}" for i, line in enumerate(slice_lines, start=start_line)]
        output = "\n".join(numbered)
        if end_line < total:
            output += f"\n... [{start_line}-{min(end_line, total)} of {total}]"
        return output
    except Exception as e:
        return f"Error: {e}"


def _iter_safe_files(target: Path):
    if target.is_file():
        yield target
        return

    for root, dirs, files in os.walk(target):
        root_path = Path(root)
        dirs[:] = sorted(
            d
            for d in dirs
            if not _is_blocked_part(d) and (root_path / d).resolve() != AGENT_DIR
        )
        for fname in sorted(files):
            if _is_blocked_part(fname):
                continue
            fpath = (root_path / fname).resolve()
            if fpath.is_relative_to(BASE_DIR) and not fpath.is_relative_to(AGENT_DIR):
                yield fpath


@tool
def search_in_files(query: str, path: str = ".", case_sensitive: bool = False, max_results: int = 20) -> str:
    """Search for an exact text substring across plain text files.

    Args:
        query: Substring to find.
        path: Relative directory or file path. Defaults to ".".
        case_sensitive: Match case if True. Defaults to False.
        max_results: Max matches to return. Defaults to 20.
    """
    try:
        if not query:
            return "Error: Empty query."

        target = _safe_path(path)
        if not target.exists():
            return f"Error: '{path}' does not exist."

        needle = query if case_sensitive else query.lower()
        matches = []

        for resolved in _iter_safe_files(target):
            if resolved.stat().st_size > MAX_TEXT_FILE_BYTES:
                continue

            with resolved.open("rb") as f:
                sample = f.read(1024)
            if b"\x00" in sample:
                continue

            text = resolved.read_text(encoding="utf-8", errors="replace")
            rel = resolved.relative_to(BASE_DIR)
            for line_no, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    matches.append(f"{rel}:{line_no}: {line.strip()}")
                    if len(matches) >= max_results:
                        matches.append(f"... [capped at {max_results}]")
                        return "\n".join(matches)

        return "\n".join(matches) if matches else "No matches."
    except Exception as e:
        return f"Error: {e}"


@tool
def replace_in_file(path: str, old_text: str, new_text: str) -> str:
    """Replace an exact unique text block in a file and update its RAG embedding.

    Args:
        path: Relative file path.
        old_text: Existing text (must appear once).
        new_text: Replacement text.
    """
    try:
        if not old_text:
            return "Error: 'old_text' cannot be empty."

        target = _safe_path(path)
        if not target.is_file():
            return f"Error: '{path}' is not a file."

        check_err = _is_binary_Or_too_large(target)
        if check_err:
            return check_err

        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            return f"Error: 'old_text' not found in '{path}'."
        if count > 1:
            return f"Error: 'old_text' matched {count} times. Add surrounding context."

        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        _sync_file_to_rag(target)
        return f"Updated '{target.relative_to(BASE_DIR)}' and re-indexed into RAG."
    except Exception as e:
        return f"Error: {e}"
