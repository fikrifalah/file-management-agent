import os
import shutil
import subprocess
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = AGENT_DIR / "build"
APP_NAME = "FileManagementAgent"


def ensure_build_dependencies() -> None:
    missing: list[str] = []
    for mod, pkg in [
        ("webview", "pywebview"),
        ("PyInstaller", "pyinstaller"),
        ("pypdf", "pypdf"),
        ("docx", "python-docx"),
        ("openpyxl", "openpyxl"),
        ("sqlite_vec", "sqlite-vec"),
        ("fastembed", "fastembed"),
    ]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"[build] Installing missing packages: {', '.join(missing)}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
    else:
        print("[build] All build and RAG dependencies are installed.")


def build_executable() -> Path:
    ensure_build_dependencies()

    sep = ";" if os.name == "nt" else ":"
    ui_dir = AGENT_DIR / "ui"
    prompt_file = AGENT_DIR / "system prompt.yaml"
    tools_file = AGENT_DIR / "tools.py"

    if not (ui_dir / "index.html").is_file():
        raise FileNotFoundError(f"Missing frontend asset: {ui_dir / 'index.html'}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name",
        APP_NAME,
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
        "--specpath",
        str(BUILD_DIR),
        "--add-data",
        f"{ui_dir}{sep}ui",
        "--add-data",
        f"{prompt_file}{sep}.",
        # smolagents @tool calls inspect.getsource() at import time, so bundle tools.py source
        "--add-data",
        f"{tools_file}{sep}.",
        "--collect-all",
        "webview",
        "--collect-all",
        "sqlite_vec",
        "--collect-all",
        "fastembed",
        "--collect-binaries",
        "onnxruntime",
        "--collect-data",
        "smolagents",
        "--copy-metadata",
        "smolagents",
        "--collect-submodules",
        "rich._unicode_data",
        "--hidden-import",
        "dotenv",
        "--hidden-import",
        "openai",
        "--hidden-import",
        "pypdf",
        "--hidden-import",
        "docx",
        "--hidden-import",
        "openpyxl",
        "--hidden-import",
        "file_parser",
        "--hidden-import",
        "rag_engine",
        "--hidden-import",
        "tools",
        "--hidden-import",
        "main",
        "--exclude-module",
        "litellm",
        "--exclude-module",
        "boto3",
        "--exclude-module",
        "botocore",
        "--exclude-module",
        "torch",
        "--exclude-module",
        "transformers",
        str(AGENT_DIR / "desktop_app.py"),
    ]

    print("[build] Running PyInstaller for pywebview desktop app with local RAG...")
    subprocess.check_call(cmd, cwd=str(AGENT_DIR))

    out_folder = DIST_DIR / APP_NAME
    if prompt_file.exists() and out_folder.exists():
        shutil.copy2(prompt_file, out_folder / "system prompt.yaml")

    models_cache = AGENT_DIR / "models_cache"
    if models_cache.exists() and out_folder.exists():
        shutil.copytree(models_cache, out_folder / "models_cache", dirs_exist_ok=True)

    exe_name = f"{APP_NAME}.exe" if os.name == "nt" else APP_NAME
    exe_path = out_folder / exe_name
    if not exe_path.exists():
        raise RuntimeError(f"Expected executable was not created at {exe_path}")

    print(f"\n[build] Success! Standalone desktop app created at:\n  {exe_path}")
    return exe_path


if __name__ == "__main__":
    build_executable()
