import json
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from smolagents import CodeAgent, OpenAIServerModel

from tools import (
    AGENT_DIR,
    create_directory,
    create_file,
    delete_file,
    get_resource_dir,
    initialize_rag_index,
    list_directories,
    move_file,
    rag_search,
    read_file,
    replace_in_file,
    search_in_files,
)

RESOURCE_DIR = get_resource_dir()
load_dotenv(AGENT_DIR / ".env")

console = Console()

GROQ_API_BASE = "https://api.groq.com/openai/v1"

PREFERRED_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct-0905",
    "moonshotai/kimi-k2-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.3-70b-versatile",
]

NON_TOOL_MARKERS = (
    "whisper",
    "tts",
    "guard",
    "playai",
    "compound",
    "allam",
    "orpheus",
)


def get_available_groq_models(api_key: str) -> list[str]:
    """Fetch models enabled for this GROQ_API_KEY and order them by reliability."""
    try:
        req = urllib.request.Request(
            f"{GROQ_API_BASE}/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "file-management-agent/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        available_ids = {
            item["id"]
            for item in data.get("data", [])
            if item.get("id") and not any(m in item["id"].lower() for m in NON_TOOL_MARKERS)
        }

        ordered: list[str] = []
        for pref in PREFERRED_MODELS:
            if pref in available_ids:
                ordered.append(pref)
                available_ids.remove(pref)

        for rest in sorted(available_ids):
            ordered.append(rest)

        return ordered if ordered else PREFERRED_MODELS.copy()
    except Exception:
        return PREFERRED_MODELS.copy()


def load_instructions() -> str | None:
    """Load system prompt from user-writable AGENT_DIR first, falling back to bundled RESOURCE_DIR."""
    for base in (AGENT_DIR, RESOURCE_DIR):
        prompt_file = base / "system prompt.yaml"
        if prompt_file.exists():
            text = prompt_file.read_text(encoding="utf-8").strip()
            if text:
                return text
    return None


def save_api_key(api_key: str, model_id: str | None = None) -> Path:
    """Save GROQ_API_KEY (and optional GROQ_MODEL) to AGENT_DIR / '.env' next to the executable."""
    api_key = api_key.strip()
    env_path = AGENT_DIR / ".env"
    lines: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip().startswith(("GROQ_API_KEY=", "GROQ_MODEL=")):
                lines.append(line)

    lines.append(f"GROQ_API_KEY={api_key}")
    os.environ["GROQ_API_KEY"] = api_key

    if model_id is not None:
        model_id = model_id.strip()
        if model_id:
            lines.append(f"GROQ_MODEL={model_id}")
            os.environ["GROQ_MODEL"] = model_id
        else:
            os.environ.pop("GROQ_MODEL", None)

    env_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return env_path


def build_agent(model_id: str, api_key: str) -> CodeAgent:
    model = OpenAIServerModel(
        model_id=model_id,
        api_base=GROQ_API_BASE,
        api_key=api_key,
        client_kwargs={"timeout": 30.0},
        temperature=0.1,
    )
    return CodeAgent(
        tools=[
            rag_search,
            list_directories,
            read_file,
            search_in_files,
            create_directory,
            create_file,
            replace_in_file,
            move_file,
            delete_file,
        ],
        model=model,
        instructions=load_instructions(),
        max_steps=10,
    )


def ensure_api_key() -> str:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key:
        return api_key

    console.print("[bold yellow]GROQ_API_KEY not found in agent/.env.[/bold yellow]")
    api_key = console.input("[bold cyan]Enter your GROQ_API_KEY:[/bold cyan] ").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is required to run the agent.")

    env_path = AGENT_DIR / ".env"
    with env_path.open("a", encoding="utf-8") as f:
        f.write(f"\nGROQ_API_KEY={api_key}\n")
    os.environ["GROQ_API_KEY"] = api_key
    console.print("[green]Saved GROQ_API_KEY to agent/.env[/green]\n")
    return api_key


def run_task_with_fallback(task: str, models: list[str], api_key: str, start_idx: int = 0) -> tuple[object | None, int]:
    for offset in range(len(models)):
        idx = (start_idx + offset) % len(models)
        model_id = models[idx]
        try:
            agent = build_agent(model_id, api_key)
            result = agent.run(task)
            return result, idx
        except Exception as e:
            console.print(f"[yellow]{model_id} error: {e}[/yellow]")
            if offset + 1 < len(models):
                next_model = models[(idx + 1) % len(models)]
                console.print(f"[yellow]Switching to {next_model}...[/yellow]")
            else:
                console.print("[bold red]All available Groq models failed for this request.[/bold red]")
    return None, start_idx


def main() -> None:
    api_key = ensure_api_key()

    console.print("[cyan]Indexing workspace files into local SQLite + sqlite-vec RAG database...[/cyan]")
    rag_stats = initialize_rag_index()
    console.print(
        f"[dim]RAG ready — indexed {rag_stats['indexed_files']} file(s) "
        f"({rag_stats['unchanged_files']} cached, {rag_stats['new_chunks']} new chunks).[/dim]"
    )

    env_model = os.getenv("GROQ_MODEL")
    if env_model:
        models = [env_model.removeprefix("groq/")]
    else:
        models = get_available_groq_models(api_key)

    current_idx = 0

    # Support one-shot CLI usage: .\agent\run.bat "list all files"
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        result, current_idx = run_task_with_fallback(task, models, api_key, current_idx)
        if result is not None:
            console.print(Panel(str(result), title=f"Agent ({models[current_idx]})", border_style="green"))
        return

    console.print(
        Panel(
            f"Active model: [bold green]{models[current_idx]}[/bold green]\n"
            f"Available fallbacks: {', '.join(models[1:5]) or 'none'}\n"
            "Type 'exit' to quit.",
            title="File Management CodeAgent (Groq)",
            style="cyan",
        )
    )

    while True:
        try:
            task = console.input("\n[bold yellow]You>[/bold yellow] ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not task:
            continue
        if task.lower() in {"exit", "quit", "q"}:
            break

        result, current_idx = run_task_with_fallback(task, models, api_key, current_idx)
        if result is not None:
            console.print(Panel(str(result), title=f"Agent ({models[current_idx]})", border_style="green"))


if __name__ == "__main__":
    main()
