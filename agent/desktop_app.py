import ctypes
import json
import os
import sys
import threading
from pathlib import Path

# In PyInstaller --windowed builds on Windows, sys.stdout and sys.stderr are None.
# Redirect them to os.devnull before importing rich/smolagents so agent logging never crashes.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import webview
from dotenv import load_dotenv

from main import (
    AGENT_DIR,
    PREFERRED_MODELS,
    RESOURCE_DIR,
    build_agent,
    get_available_groq_models,
    load_instructions,
    save_api_key,
)
from tools import get_base_dir, set_base_dir

load_dotenv(AGENT_DIR / ".env")
CONFIG_PATH = AGENT_DIR / "config.json"


def _load_local_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_local_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# Restore persisted workspace folder if configured
_initial_cfg = _load_local_config()
if _initial_cfg.get("workspace"):
    try:
        set_base_dir(_initial_cfg["workspace"])
    except Exception:
        pass


class BridgeApi:
    """Asynchronous Python <-> JavaScript bridge exposed as window.pywebview.api."""

    def __init__(self) -> None:
        self._window: webview.Window | None = None
        self._lock = threading.Lock()
        self._is_running = False
        self.chat_history: list[tuple[str, str]] = []
        self.models: list[str] = PREFERRED_MODELS.copy()
        self.current_idx = 0

        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if api_key:
            threading.Thread(target=self._refresh_models, args=(api_key,), daemon=True).start()

    def attach_window(self, window: webview.Window) -> None:
        self._window = window

    def _refresh_models(self, api_key: str) -> None:
        env_model = os.getenv("GROQ_MODEL", "").strip()
        if env_model:
            models = [env_model.removeprefix("groq/")]
        else:
            models = get_available_groq_models(api_key)
        if models:
            self.models = models
            self.current_idx = 0

    def get_settings(self) -> dict:
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        groq_model = os.getenv("GROQ_MODEL", "").strip()
        masked = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else ("*" * len(api_key))
        active_model = groq_model or (self.models[self.current_idx] if self.models else PREFERRED_MODELS[0])
        return {
            "has_api_key": bool(api_key),
            "groq_api_key": api_key,
            "masked_key": masked,
            "groq_model": groq_model,
            "workspace": str(get_base_dir()),
            "active_model": active_model,
            "models": self.models,
        }

    def save_settings(self, data: dict) -> dict:
        try:
            api_key = str(data.get("groq_api_key", "")).strip()
            if not api_key:
                return {"ok": False, "error": "GROQ_API_KEY cannot be empty."}

            groq_model = str(data.get("groq_model", "")).strip().removeprefix("groq/")
            workspace = str(data.get("workspace", "")).strip()

            if workspace:
                resolved_ws = set_base_dir(workspace)
                cfg = _load_local_config()
                cfg["workspace"] = str(resolved_ws)
                _save_local_config(cfg)

            save_api_key(api_key, groq_model)
            self._refresh_models(api_key)
            return {"ok": True, "settings": self.get_settings()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def choose_workspace(self) -> dict:
        if not self._window:
            return {"ok": False}
        result = self._window.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=str(get_base_dir()),
        )
        if result and len(result) > 0:
            chosen = result[0]
            resolved_ws = set_base_dir(chosen)
            cfg = _load_local_config()
            cfg["workspace"] = str(resolved_ws)
            _save_local_config(cfg)
            return {"ok": True, "workspace": str(resolved_ws)}
        return {"ok": False}

    def reset_chat(self) -> dict:
        self.chat_history.clear()
        return {"ok": True}

    def send_message(self, text: str) -> dict:
        prompt = (text or "").strip()
        if not prompt:
            return {"started": False}

        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            return {"started": False, "missing_key": True}

        with self._lock:
            if self._is_running:
                return {"started": False, "busy": True}
            self._is_running = True

        threading.Thread(
            target=self._execute_agent_bg,
            args=(prompt, api_key),
            daemon=True,
        ).start()
        return {"started": True}

    def _emit_js(self, fn_name: str, payload: object) -> None:
        if not self._window:
            return
        serialized = json.dumps(payload)
        self._window.evaluate_js(f"window.{fn_name} && window.{fn_name}({serialized});")

    def _build_contextual_prompt(self, prompt: str) -> str:
        if not self.chat_history:
            return prompt
        # Include recent turns concisely so follow-ups work naturally without bloating tokens
        recent = self.chat_history[-3:]
        ctx_lines = ["Recent conversation context:"]
        for u_msg, a_msg in recent:
            short_a = a_msg if len(a_msg) <= 300 else a_msg[:300] + "..."
            ctx_lines.append(f"- User: {u_msg}\n  Assistant: {short_a}")
        ctx_lines.append(f"\nCurrent user request:\n{prompt}")
        return "\n".join(ctx_lines)

    def _execute_agent_bg(self, prompt: str, api_key: str) -> None:
        try:
            env_model = os.getenv("GROQ_MODEL", "").strip()
            models = [env_model.removeprefix("groq/")] if env_model else (self.models or PREFERRED_MODELS.copy())
            full_task = self._build_contextual_prompt(prompt)
            last_error: Exception | None = None

            for offset in range(len(models)):
                idx = (self.current_idx + offset) % len(models)
                model_id = models[idx]
                self._emit_js("__onAgentStatus", f"Running with {model_id}...")
                try:
                    agent = build_agent(model_id, api_key)
                    result = str(agent.run(full_task))
                    self.current_idx = idx
                    self.chat_history.append((prompt, result))
                    self._emit_js("__onAgentReply", {"reply": result, "model": model_id})
                    return
                except Exception as e:
                    last_error = e
                    if offset + 1 < len(models):
                        next_model = models[(idx + 1) % len(models)]
                        self._emit_js("__onAgentStatus", f"{model_id} hit limit/error — switching to {next_model}...")

            self._emit_js("__onAgentError", {"error": f"All available Groq models failed. Last error: {last_error}"})
        finally:
            with self._lock:
                self._is_running = False


def _apply_windows_dark_titlebar(window_title: str) -> None:
    if os.name != "nt":
        return
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if hwnd:
            value = ctypes.c_int(1)
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 10 20H1+ / Windows 11)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def get_ui_entry() -> Path:
    for base in (RESOURCE_DIR, AGENT_DIR):
        candidate = base / "ui" / "index.html"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Could not find bundled ui/index.html in {RESOURCE_DIR} or {AGENT_DIR}")


def main() -> None:
    self_test = "--self-test" in sys.argv
    ui_html = get_ui_entry()

    if self_test:
        assert ui_html.is_file(), "ui/index.html missing"
        assert load_instructions(), "system prompt.yaml missing"
        os.environ.setdefault("GROQ_API_KEY", "self_test_dummy_key")
        bridge = BridgeApi()
        assert bridge.get_settings()["has_api_key"] is True
        assert build_agent(PREFERRED_MODELS[0], "self_test_dummy_key") is not None
        print("SELF_TEST_OK")
        return

    bridge = BridgeApi()
    title = "File Management Agent"
    window = webview.create_window(
        title=title,
        url=ui_html.as_uri(),
        js_api=bridge,
        width=1000,
        height=720,
        min_size=(720, 520),
        resizable=True,
        background_color="#0d0d10",
    )
    bridge.attach_window(window)

    def on_shown() -> None:
        _apply_windows_dark_titlebar(title)

    window.events.shown += on_shown
    webview.start(debug=False)


if __name__ == "__main__":
    main()
