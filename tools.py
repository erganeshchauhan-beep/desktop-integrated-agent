"""Local tool implementations: Windows OS control, GUI navigation, shell access.

Every public tool wraps its work in try/except and returns a descriptive
string on success or failure, so a tool error never crashes the agent loop.
"""

import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

try:
    import winreg  # Windows-only
except ImportError:  # pragma: no cover - non-Windows fallback
    winreg = None

# Friendly aliases -> executable / shell names.
APP_ALIASES = {
    "spotify": "Spotify",
    "chrome": "chrome",
    "google chrome": "chrome",
    "firefox": "firefox",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "terminal": "wt",
    "windows terminal": "wt",
    "cmd": "cmd",
    "command prompt": "cmd",
    "powershell": "powershell",
    "explorer": "explorer",
    "file explorer": "explorer",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "vscode": "code",
    "vs code": "code",
    "visual studio code": "code",
    "paint": "mspaint",
    "word": "winword",
    "excel": "excel",
    "snipping tool": "snippingtool",
}

# Substrings that mark a shell command as too destructive for the agent.
_BLOCKED_COMMAND_TOKENS = (
    "format ",
    "del /",
    "erase /",
    "rmdir /s",
    "rd /s",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "remove-item -recurse",
    "reg delete",
    "bcdedit",
    "diskpart",
    "takeown",
    "icacls",
)


# ---------------------------------------------------------------------------
# App launching — dynamic discovery with executable verification
# ---------------------------------------------------------------------------
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_BAD_NAME_TOKENS = (
    "uninstall", "unins", "setup", "installer", "update", "updater",
    "helper", "crash", "reporter", "report", "vcredist", "redist",
    "readme", "license", "eula", "diagnostic", "telemetry",
    "repair", "modify", "remove", "elevate", "activation",
)

_SKIP_DIRS = {
    "$recycle.bin", "windowsapps", "installer", "temp", "cache",
    "node_modules", "windows", "system volume information",
}

_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
_UNINSTALL_PATHS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)

_MAX_WALK_DEPTH = 4
_MAX_WALK_ENTRIES = 20000

_INDEX_CACHE: dict = {}


def _cached(key, builder):
    if key not in _INDEX_CACHE:
        try:
            _INDEX_CACHE[key] = list(builder())
        except Exception:
            _INDEX_CACHE[key] = []
    return _INDEX_CACHE[key]


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _is_pe_executable(path) -> bool:
    """True only if `path` is an existing .exe with a valid MZ/PE header."""
    try:
        candidate = Path(path)
        if candidate.suffix.lower() != ".exe":
            return False
        if not candidate.is_file():
            return False
        with candidate.open("rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            raw_offset = handle.read(4)
            if len(raw_offset) != 4:
                return False
            handle.seek(int.from_bytes(raw_offset, "little"))
            return handle.read(4) == b"PE\0\0"
    except (OSError, ValueError):
        return False


def _resolve_lnk(lnk_path: str) -> Optional[str]:
    try:
        import win32com.client
        target = win32com.client.Dispatch("WScript.Shell").CreateShortcut(lnk_path).TargetPath
        if target:
            return str(target)
    except Exception:
        pass
    try:
        safe = str(lnk_path).replace("'", "''")
        script = (
            "$sh = New-Object -ComObject WScript.Shell; "
            f"$s = $sh.CreateShortcut('{safe}'); "
            "if ($s.TargetPath) { Write-Output $s.TargetPath }"
        )
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        )
        target = (completed.stdout or "").strip()
        return target or None
    except Exception:
        return None


def _iter_start_menu_lnks():
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if not root.is_dir():
            continue
        try:
            yield from root.rglob("*.lnk")
        except OSError:
            continue


def _iter_app_paths():
    if os.name != "nt" or winreg is None:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, _APP_PATHS_KEY, 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, sub_name) as sub:
                            exe, _ = winreg.QueryValueEx(sub, "")
                    except OSError:
                        continue
                    if exe:
                        yield sub_name, str(exe)


def _iter_installed_apps():
    if os.name != "nt" or winreg is None:
        return
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in _UNINSTALL_PATHS:
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    root = winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view)
                except OSError:
                    continue
                with root:
                    index = 0
                    while True:
                        try:
                            sub_name = winreg.EnumKey(root, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(root, sub_name) as sub:
                                name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                icon, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                        except OSError:
                            continue
                        if name and icon:
                            yield str(name), str(icon)


def _iter_start_apps():
    if os.name != "nt":
        return
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-StartApps | ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }"],
            capture_output=True, text=True, timeout=25, creationflags=_NO_WINDOW,
        )
    except Exception:
        return
    for line in (completed.stdout or "").splitlines():
        if "\t" not in line:
            continue
        name, app_id = line.split("\t", 1)
        name, app_id = name.strip(), app_id.strip()
        if name and app_id:
            yield name, app_id


def _program_files_roots():
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)",
                "LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(var)
        if value:
            root = Path(value)
            if root.is_dir():
                yield root


def _walk_for_exe(query_norm: str):
    found = []
    visited = 0
    for root in _program_files_roots():
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth > _MAX_WALK_DEPTH:
                continue
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                visited += 1
                if visited > _MAX_WALK_ENTRIES:
                    return found
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() not in _SKIP_DIRS:
                            stack.append((Path(entry.path), depth + 1))
                    elif entry.name.lower().endswith(".exe"):
                        if query_norm in _normalize(Path(entry.name).stem):
                            found.append(Path(entry.path))
                except OSError:
                    continue
    return found


def _exe_from_icon(icon: str) -> Optional[str]:
    if not icon:
        return None
    value = str(icon).strip().strip('"')
    if "," in value:
        value = value.rsplit(",", 1)[0].strip().strip('"')
    if value.lower().endswith(".exe") and os.path.isfile(value):
        return value
    return None


def _score(query_norm: str, candidate_name: str, candidate_path: str = "") -> int:
    name_norm = _normalize(candidate_name)
    if not query_norm or not name_norm:
        return 0
    if name_norm == query_norm:
        score = 100
    elif name_norm.startswith(query_norm):
        score = 85
    elif query_norm in name_norm:
        score = 70
    elif name_norm in query_norm:
        score = 55
    else:
        return 0
    haystack = f"{candidate_name} {candidate_path}".lower()
    if any(token in haystack for token in _BAD_NAME_TOKENS):
        score -= 45
    return score if score > 0 else 0


def _resolve_executable(target: str) -> Optional[str]:
    if not target:
        return None
    if target.endswith(":"):
        return target
    if os.path.exists(target):
        return target
    found = shutil.which(target)
    if found:
        return found
    if not target.lower().endswith(".exe"):
        found = shutil.which(f"{target}.exe")
        if found:
            return found
    return None


def find_app(app_name: str, allow_store_apps: bool = True):
    query = _normalize(app_name)
    if not query:
        return None
    lowered = app_name.strip().lower()

    alias = APP_ALIASES.get(lowered)
    if alias:
        if alias.endswith(":"):
            return ("uri", alias, app_name.strip())
        resolved = _resolve_executable(alias)
        if resolved and _is_pe_executable(resolved):
            return ("exe", resolved, app_name.strip())

    resolved = _resolve_executable(app_name.strip())
    if resolved and _is_pe_executable(resolved):
        return ("exe", resolved, app_name.strip())

    candidates = []

    for sub_name, exe in _cached("app_paths", _iter_app_paths):
        stem = Path(sub_name).stem
        score = _score(query, stem, exe)
        if score and _is_pe_executable(exe):
            candidates.append(((score, -len(stem)), "exe", exe, stem))

    for display_name, icon in _cached("installed", _iter_installed_apps):
        exe = _exe_from_icon(icon)
        if not exe or not _is_pe_executable(exe):
            continue
        score = _score(query, display_name, exe)
        if score:
            candidates.append(((score, -len(display_name)), "exe", exe, display_name))

    lnk_budget = 40
    for lnk in _cached("start_menu_lnks", _iter_start_menu_lnks):
        stem = lnk.stem
        score = _score(query, stem)
        if not score:
            continue
        if lnk_budget <= 0:
            break
        lnk_budget -= 1
        target = _resolve_lnk(str(lnk))
        if target and _is_pe_executable(target):
            candidates.append(((score, -len(stem)), "exe", target, stem))

    best_so_far = max((c[0][0] for c in candidates), default=0)

    if best_so_far < 85:
        for exe in _walk_for_exe(query):
            if not _is_pe_executable(exe):
                continue
            stem = exe.stem
            score = _score(query, stem, str(exe))
            if score:
                candidates.append(((score, -len(stem)), "exe", str(exe), stem))

    if allow_store_apps:
        for start_name, app_id in _cached("start_apps", _iter_start_apps):
            score = _score(query, start_name)
            if score:
                candidates.append(((score - 10, -len(start_name)), "aumid", app_id, start_name))

    candidates = [c for c in candidates if c[0][0] > 0]
    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, kind, target, label = candidates[0]
    return (kind, target, label)


def _launch_executable(exe_path: str) -> None:
    if not _is_pe_executable(exe_path):
        raise ValueError(f"'{exe_path}' is not a valid executable; launch aborted.")
    if os.name == "nt":
        os.startfile(exe_path)
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, exe_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_app(app_name: str, allow_store_apps: bool = True) -> str:
    try:
        name = (app_name or "").strip()
        if not name:
            return "No application name was provided."

        match = find_app(name, allow_store_apps=allow_store_apps)
        if match is None:
            return (
                f"Could not find an installed application matching '{name}'. "
                f"Nothing was launched."
            )

        kind, target, label = match

        if kind == "exe":
            _launch_executable(target)
            return f"Launched '{label}' from {target}."

        if kind == "uri":
            os.startfile(target)
            return f"Opened '{label}' via the '{target}' shell handler."

        if kind == "aumid":
            subprocess.Popen(
                ["explorer.exe", f"shell:AppsFolder\\{target}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
            )
            return f"Launched Store app '{label}' (AppID {target})."

        return f"Internal error: unknown launch kind '{kind}'."

    except Exception as exc:
        return f"Failed to open '{app_name}': {exc}"


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
def open_url(url: str) -> str:
    try:
        url = (url or "").strip()
        if not url:
            return "No URL was provided."
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if webbrowser.open(url):
            return f"Opened {url} in the default browser."
        return f"Could not find a browser to open {url}."
    except Exception as exc:
        return f"Failed to open URL '{url}': {exc}"


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------
def run_windows_command(command: str) -> str:
    try:
        command = (command or "").strip()
        if not command:
            return "No command was provided."
        lowered = command.lower()
        for token in _BLOCKED_COMMAND_TOKENS:
            if token in lowered:
                return f"Refused to run potentially destructive command: '{command}'."
        completed = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        if completed.returncode == 0:
            return output or f"Command '{command}' completed successfully with no output."
        return (
            f"Command '{command}' exited with code {completed.returncode}. "
            f"Output: {output or '(none)'}"
        )
    except subprocess.TimeoutExpired:
        return f"Command '{command}' timed out after 30 seconds."
    except Exception as exc:
        return f"Failed to run command '{command}': {exc}"


# ---------------------------------------------------------------------------
# GUI automation
# ---------------------------------------------------------------------------
def gui_click_or_type(
    action: str,
    text: str = "",
    x: int = None,
    y: int = None,
    keys: list = None,
) -> str:
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        action = (action or "").strip().lower()

        if action == "move":
            if x is None or y is None:
                return "Move action requires both x and y coordinates."
            pyautogui.moveTo(int(x), int(y), duration=0.2)
            return f"Mouse moved to ({int(x)}, {int(y)})."

        if action == "click":
            if x is not None and y is not None:
                pyautogui.click(int(x), int(y))
                return f"Clicked at ({int(x)}, {int(y)})."
            pyautogui.click()
            return "Clicked at the current mouse position."

        if action == "double_click":
            if x is not None and y is not None:
                pyautogui.doubleClick(int(x), int(y))
                return f"Double-clicked at ({int(x)}, {int(y)})."
            pyautogui.doubleClick()
            return "Double-clicked at the current mouse position."

        if action == "right_click":
            if x is not None and y is not None:
                pyautogui.rightClick(int(x), int(y))
                return f"Right-clicked at ({int(x)}, {int(y)})."
            pyautogui.rightClick()
            return "Right-clicked at the current mouse position."

        if action == "type":
            if not text:
                return "Type action requires non-empty text."
            if x is not None and y is not None:
                pyautogui.click(int(x), int(y))
                time.sleep(0.2)
            pyautogui.typewrite(text, interval=0.02)
            return f"Typed {len(text)} characters."

        if action in ("press", "hotkey"):
            if not keys:
                return "Press/hotkey action requires a keys list, e.g. ['ctrl', 'c']."
            keys = [str(k).lower() for k in keys]
            if action == "hotkey" or len(keys) > 1:
                pyautogui.hotkey(*keys)
                return f"Pressed hotkey {'+'.join(keys)}."
            pyautogui.press(keys[0])
            return f"Pressed key '{keys[0]}'."

        if action == "scroll":
            raw = str(text).strip()
            amount = int(raw) if raw.lstrip("-").isdigit() else -3
            pyautogui.scroll(amount)
            direction = "up" if amount > 0 else "down"
            return f"Scrolled {direction} by {abs(amount)}."

        return (
            f"Unknown GUI action '{action}'. Supported actions: move, click, "
            f"double_click, right_click, type, press, hotkey, scroll."
        )
    except Exception as exc:
        return f"GUI action '{action}' failed: {exc}"


# ---------------------------------------------------------------------------
# Screenshots
# ---------------------------------------------------------------------------
def take_screenshot(filename: str = "screen.png") -> str:
    try:
        import pyautogui
        filename = (filename or "screen.png").strip()
        if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
            filename += ".png"
        path = Path(filename)
        if not path.is_absolute():
            path = Path.cwd() / "screenshots" / path.name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = pyautogui.screenshot()
        image.save(path)
        return f"Screenshot saved to {path.resolve()}."
    except Exception as exc:
        return f"Failed to take screenshot: {exc}"


# ---------------------------------------------------------------------------
# Gemini tool declarations + dispatcher
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS = [
    {
        "name": "open_app",
        "description": (
            "Launch a desktop application on this Windows PC by name, e.g. "
            "'spotify', 'chrome', 'notepad', 'excel'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_name": {
                    "type": "string",
                    "description": "Name of the application to open.",
                }
            },
            "required": ["app_name"],
        },
    },
    {
        "name": "open_url",
        "description": "Open a website/URL in the user's default browser.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to open, with or without the https:// prefix.",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_windows_command",
        "description": (
            "Run a safe, read-only or non-destructive CMD/PowerShell command on "
            "Windows (e.g. 'ipconfig', 'dir', 'systeminfo') and return its output. "
            "Never use it for destructive operations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "gui_click_or_type",
        "description": (
            "Control the mouse and keyboard: move or click at coordinates, type "
            "text, or press keys/hotkeys such as ['ctrl', 'c']."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "One of: move, click, double_click, right_click, type, "
                        "press, hotkey, scroll."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Text to type (for 'type') or scroll amount (for 'scroll').",
                },
                "x": {"type": "integer", "description": "Optional X coordinate."},
                "y": {"type": "integer", "description": "Optional Y coordinate."},
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keys for press/hotkey, e.g. ['ctrl', 'c'].",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Capture the current screen to an image file for desktop state awareness.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Image file name to save, defaults to screen.png.",
                }
            },
        },
    },
]

_TOOL_REGISTRY = {
    "open_app": open_app,
    "open_url": open_url,
    "run_windows_command": run_windows_command,
    "gui_click_or_type": gui_click_or_type,
    "take_screenshot": take_screenshot,
}


def execute_tool(name: str, args: dict) -> str:
    func = _TOOL_REGISTRY.get(name)
    if func is None:
        available = ", ".join(sorted(_TOOL_REGISTRY))
        return f"Unknown tool '{name}'. Available tools: {available}."
    try:
        args = args or {}
        if not isinstance(args, dict):
            return f"Tool '{name}' received invalid arguments: {args!r}"
        return str(func(**args))
    except TypeError as exc:
        return f"Bad arguments for tool '{name}': {exc}"
    except Exception as exc:
        return f"Tool '{name}' raised an unexpected error: {exc}"