"""Conversation memory: a JSON-backed history with a sliding context window."""

import json
from datetime import datetime
from typing import Optional

import config


def load_history() -> list:
    """Load history.json safely. Returns [] when missing or corrupt."""
    try:
        if not config.HISTORY_FILE.exists():
            return []
        with open(config.HISTORY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_history(user_msg: str, agent_msg: str, tool_used: Optional[str] = None) -> list:
    """Append one interaction turn and persist it to disk.

    Writes atomically (temp file + replace) and trims the stored history to
    the configured maximum so the file never grows without bound. Memory is
    best-effort: a disk failure never crashes the agent.
    """
    history = load_history()
    history.append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "user": user_msg,
            "agent": agent_msg,
            "tool": tool_used,
        }
    )
    history = history[-config.MAX_HISTORY_TURNS :]
    tmp_path = config.HISTORY_FILE.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(history, fh, ensure_ascii=False, indent=2)
        tmp_path.replace(config.HISTORY_FILE)
    except OSError:
        pass
    return history


def get_recent_context(limit: int = 6) -> list:
    """Return the most recent `limit` turns for use as prompt context."""
    if limit <= 0:
        return []
    return load_history()[-limit:]
