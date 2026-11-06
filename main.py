"""Entry point: a clean terminal REPL orchestrating the pipeline.

Flow per turn: router.decide -> (execute_tool) -> persona.speak -> memory.
"""
import memory
import persona
import router
import time
from tools import execute_tool

BANNER = (
    "============================================================\n"
    "  Desktop Automation Agent\n"
    "  Type a request, or 'exit' / 'quit' to shut down.\n"
    "============================================================"
)

EXIT_WORDS = {"exit", "quit"}


def handle_turn(user_input: str) -> str:
    """Run one full pipeline turn and return the persona's spoken reply."""
    history = memory.get_recent_context()
    decision = router.decide(user_input, history)

    if decision.get("type") == "tool":
        tool_name = decision.get("name", "")
        tool_args = decision.get("args") or {}
        print(f"  [tool] {tool_name}({tool_args})")
        raw_result = execute_tool(tool_name, tool_args)
        reply = persona.speak(user_input, raw_result)
        memory.save_history(user_input, reply, tool_used=tool_name)
        return reply

    content = decision.get("content", "")
    reply = persona.speak(user_input, content)
    memory.save_history(user_input, reply)
    return reply


def main() -> None:
    print(BANNER)
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAgent: Powering down. Goodbye, sir.")
            break

        if not user_input:
            continue
        if user_input.lower() in EXIT_WORDS:
            print("Agent: Powering down. Goodbye, sir.")
            break

        try:
            reply = handle_turn(user_input)
        except Exception as exc:
            # Router/API failures land here; the agent keeps running.
            reply = f"I hit an unexpected snag: {exc}"
        print(f"Agent: {reply}")

import time

def decide(user_input: str, history: list) -> dict:
    client = _get_client()
    contents = _build_contents(user_input, history)
    config_obj = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        tools=[types.Tool(function_declarations=TOOL_DEFINITIONS)],
        temperature=0.2,
    )

    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=contents,
                config=config_obj,
            )
            break
        except Exception as e:
            if "503" in str(e) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise e

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
    
if __name__ == "__main__":
    main()
