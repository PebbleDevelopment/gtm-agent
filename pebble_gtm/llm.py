"""Claude plumbing shared by every agent: the client and one agent loop.

An agent here is: a system prompt, some tools (Python functions), and a loop that lets
Claude call tools until it's done. Every agent in this project runs through run_agent(),
so there is one place to read to understand how they all work.

Needs `pip install anthropic` and ANTHROPIC_API_KEY (or an `ant auth login` profile).
Set PEBBLE_OFFLINE=1 to force the template/rules fallbacks.
"""

import json
import os

MODEL = "claude-opus-5-5"


def available():
    if os.environ.get("PEBBLE_OFFLINE") == "1":
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    import anthropic
    return anthropic.Anthropic()


def tool(name, description, properties=None, required=None):
    """Shorthand for a client tool definition."""
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or []}}


WEB_TOOLS = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
]


def run_agent(system, messages, tools, handlers, effort="high", max_steps=15):
    """Let Claude work until it stops calling tools. Returns (final_text, messages).

    `handlers` maps tool name -> Python function taking the tool's input as kwargs.
    A handler that raises becomes an is_error tool result, so Claude can see what went wrong and adjust.
    """
    client = _client()
    messages = list(messages)
    text = ""
    for _ in range(max_steps):
        # Server-side fallback: if the model declines on a policy check, the API retries on a fallback model.
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": effort}, system=system, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": response.content})
        text = "".join(b.text for b in response.content if b.type == "text").strip() or text

        if response.stop_reason == "pause_turn":       # long server-side web search; resume it
            continue
        if response.stop_reason == "refusal":
            return "(Claude declined this one.)", messages
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            return text, messages

        results = []
        for call in calls:
            try:
                out = handlers[call.name](**call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": out if isinstance(out, str) else json.dumps(out, default=str)})
            except Exception as e:
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"Error: {e}", "is_error": True})
        messages.append({"role": "user", "content": results})
    return text or "(stopped: too many steps)", messages
