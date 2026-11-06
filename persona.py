"""Persona layer: rewrites raw machine outcomes in-character via a local LLM.

Supports Ollama's native /api/chat endpoint (default) and any
OpenAI-compatible server such as LM Studio (endpoint containing /v1).
Falls back to a canned in-character line when the local server is offline.
"""

import random

import requests

import config

_FALLBACK_OPENERS = [
    "Done.",
    "Consider it handled.",
    "Right away - finished.",
    "Task complete.",
]


def _build_messages(context: str, action_result: str) -> list:
    """Format the persona prompt: user request + raw machine outcome."""
    user_content = (
        f'User request: "{context}"\n'
        f'Raw machine outcome: "{action_result}"\n\n'
        "Transform this into your in-character status report now."
    )
    return [
        {"role": "system", "content": config.PERSONA_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _extract_ollama(payload: dict) -> str:
    message = payload.get("message") or {}
    return (message.get("content") or "").strip()


def _extract_openai(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


def _is_openai_compatible(endpoint: str) -> bool:
    return endpoint.endswith("/v1") or "/v1/" in endpoint


def speak(context: str, action_result: str) -> str:
    """Rewrite the raw outcome in the agent's voice; degrade gracefully."""
    endpoint = config.LOCAL_MODEL_ENDPOINT.rstrip("/")
    messages = _build_messages(context, action_result)
    try:
        if _is_openai_compatible(endpoint):
            # LM Studio / OpenAI-compatible server.
            url = f"{endpoint}/chat/completions" if endpoint.endswith("/v1") else endpoint
            payload = {
                "model": config.LOCAL_MODEL_NAME,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 150,
            }
            response = requests.post(
                url, json=payload, timeout=config.LOCAL_MODEL_TIMEOUT
            )
            response.raise_for_status()
            text = _extract_openai(response.json())
        else:
            # Ollama native /api/chat.
            payload = {
                "model": config.LOCAL_MODEL_NAME,
                "messages": messages,
                "stream": False,
            }
            response = requests.post(
                endpoint, json=payload, timeout=config.LOCAL_MODEL_TIMEOUT
            )
            response.raise_for_status()
            text = _extract_ollama(response.json())
        if text:
            return text
    except (requests.exceptions.RequestException, ValueError, KeyError):
        # Local model offline, slow, or malformed - fall through to fallback.
        pass
    opener = random.choice(_FALLBACK_OPENERS)
    return f"{opener} {action_result}"
