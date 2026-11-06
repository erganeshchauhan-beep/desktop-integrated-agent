"""Routing brain: sends the query + recent history to the Gemini API with
native tool declarations and normalizes the response into a decision dict."""

from typing import Optional

from google import genai
from google.genai import types

import config
from tools import TOOL_DEFINITIONS

SYSTEM_INSTRUCTION = (
    "You are the routing layer of a Windows desktop automation agent. Decide "
    "whether the user's message requires an action on the PC. If it does, call "
    "exactly one of the provided tools with precise, minimal arguments. If it "
    "is conversation, a question, or anything that needs no local action, "
    "answer briefly in plain text and call no tool. Never claim an action was "
    "performed - you only decide and dispatch."
)

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    """Lazily create the Gemini client so a missing key fails with clarity."""
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to a .env file next to "
                "config.py or export it as an environment variable."
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _build_contents(user_input: str, history: list) -> list:
    """Convert stored turns + the new message into Gemini content objects."""
    contents = []
    for turn in (history or [])[-config.CONTEXT_WINDOW_TURNS:]:
        user_text = (turn.get("user") or "").strip()
        agent_text = (turn.get("agent") or "").strip()
        if user_text:
            contents.append(
                types.Content(role="user", parts=[types.Part(text=user_text)])
            )
        if agent_text:
            contents.append(
                types.Content(role="model", parts=[types.Part(text=agent_text)])
            )
    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_input)])
    )
    return contents


def decide(user_input: str, history: list) -> dict:
    """Route one user turn.

    Returns {"type": "tool", "name": ..., "args": {...}} when Gemini issues a
    function call, or {"type": "chat", "content": ...} for a plain-text reply.
    """
    client = _get_client()
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_build_contents(user_input, history),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            tools=[types.Tool(function_declarations=TOOL_DEFINITIONS)],
            temperature=0.2,
        ),
    )

    # Prefer an explicit function call anywhere in the candidate parts.
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None and getattr(call, "name", None):
                return {
                    "type": "tool",
                    "name": call.name,
                    "args": dict(call.args or {}),
                }

    text = (getattr(response, "text", "") or "").strip()
    return {"type": "chat", "content": text or "Gemini returned an empty response."}
