"""Central configuration for the desktop automation agent."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Gemini (router brain)
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv(
    "GEMINI_API_KEY",
    "YOukeyhere",
)
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# ---------------------------------------------------------------------------
# Local persona model (LM Studio)
# ---------------------------------------------------------------------------
LOCAL_MODEL_ENDPOINT: str = os.getenv(
    "LOCAL_MODEL_ENDPOINT", "http://localhost:1234/v1"
)
LOCAL_MODEL_NAME: str = os.getenv("LOCAL_MODEL_NAME", "l3-8b-stheno-v3.2")
LOCAL_MODEL_TIMEOUT: int = int(os.getenv("LOCAL_MODEL_TIMEOUT", "30"))

PERSONA_SYSTEM_PROMPT: str = os.getenv(
    "PERSONA_SYSTEM_PROMPT",
    (
        "You are the voice of a sleek, highly efficient desktop automation "
        "agent - a witty, loyal digital butler and technical operator. You "
        "receive (1) what the user asked for and (2) the raw machine outcome "
        "of the action. Transform that into a short, in-character status "
        "report. Rules: 1-3 sentences maximum. Be sharp, confident, and "
        "lightly witty (for example: 'Opening Spotify... opened. What next, "
        "sir?'). Confirm exactly what happened - never invent actions or "
        "results that are not in the machine outcome. If the outcome reports "
        "an error, acknowledge it plainly and suggest the next step. No "
        "emojis, no markdown, no lists. Speak directly to the user."
    ),
)

# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
HISTORY_FILE: Path = Path(
    os.getenv("HISTORY_FILE", str(BASE_DIR / "history.json"))
)
MAX_HISTORY_TURNS: int = int(os.getenv("MAX_HISTORY_TURNS", "50"))
CONTEXT_WINDOW_TURNS: int = int(os.getenv("CONTEXT_WINDOW_TURNS", "6"))