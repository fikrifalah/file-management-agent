import shutil
from pathlib import Path
from smolagents import tool

AGENT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(__file__).resolve().parents[1]
BLOCKED_NAMES = {".env", ".git", ".venv", "venv", "node_modules", "__pycache__"}


def _safe_path(user_path: str) -> Path:
    target = (BASE_DIR / user_path).resolve()
    if not target.is_relative_to(BASE_DIR):
        raise ValueError(f"Access denied: '{user_path}' is outside the project root.")
    if target.is_relative_to(AGENT_DIR) or any(part in BLOCKED_NAMES for part in target.parts):
        raise ValueError(f"Access denied: '{user_path}' is a protected path.")
    return target


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
            if item.resolve() == AGENT_DIR or item.name in BLOCKED_NAMES:
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
        return f"Created directory '{path}'."
    except Exception as e:
        return f"Error: {e}"


@tool
def create_file(path: str, suffix: str = "", content: str = "", overwrite: bool = False) -> str:
    """Create a file (and parent folders automatically) with optional suffix and content.

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
        if target.exists() and not overwrite:
            return f"Error: '{path}' exists. Set overwrite=True to replace."
        if target.is_dir():
            return f"Error: '{path}' is a directory."

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Created file '{target.relative_to(BASE_DIR)}'."
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
        if src == BASE_DIR:
            return "Error: Cannot move project root."

        if dst.is_dir():
            dst = _safe_path(str((dst / src.name).relative_to(BASE_DIR)))

        if dst.exists():
            return f"Error: Destination '{destination}' already exists."

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
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
        if target == BASE_DIR:
            return "Error: Cannot delete project root."

        if target.is_dir():
            if any(target.iterdir()) and not recursive:
                return f"Error: '{path}' is not empty. Set recursive=True."
            shutil.rmtree(target) if recursive else target.rmdir()
            return f"Deleted directory '{path}'."

        target.unlink()
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


@tool
def search_in_files(query: str, path: str = ".", case_sensitive: bool = False, max_results: int = 20) -> str:
    """Search for a text substring across files.

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

        files = [target] if target.is_file() else sorted(target.rglob("*"))
        needle = query if case_sensitive else query.lower()
        matches = []

        for file_path in files:
            if not file_path.is_file():
                continue
            resolved = file_path.resolve()
            if resolved.is_relative_to(AGENT_DIR) or any(part in BLOCKED_NAMES for part in resolved.parts):
                continue

            raw = file_path.read_bytes()
            if b"\x00" in raw[:1024]:
                continue

            rel = file_path.relative_to(BASE_DIR)
            for line_no, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
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
    """Replace an exact unique text block in a file.

    Args:
        path: Relative file path.
        old_text: Existing text (must appear once).
        new_text: Replacement text.
    """
    try:
        target = _safe_path(path)
        if not target.is_file():
            return f"Error: '{path}' is not a file."

        content = target.read_text(encoding="utf-8")
        count = content.count(old_text)
        if count == 0:
            return f"Error: 'old_text' not found in '{path}'."
        if count > 1:
            return f"Error: 'old_text' matched {count} times. Add surrounding context."

        target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Updated '{target.relative_to(BASE_DIR)}'."
    except Exception as e:
        return f"Error: {e}"
