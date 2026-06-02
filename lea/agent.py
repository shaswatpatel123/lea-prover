"""Lea agent — the core loop. Model calls tools until done.

`run_events()` is the generator core: it yields a typed event stream and never
prints. `run()` is a backward-compatible wrapper that drains those events through
the default stdout renderer and returns the final text (and optional transcript),
so existing callers (CLI, eval) keep working unchanged.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import LeaConfig, load_config
from .prompt import load_system_prompt
from .providers import stream, TextDelta, ToolCall, Done, _ToolMeta, Usage
from .tools import TOOLS_SCHEMA, TOOL_HANDLERS
from .events import (
    SessionResumed,
    TurnStarted,
    AssistantTextDelta,
    ToolCalled,
    ToolResulted,
    UsageUpdated,
    Finished,
)
from .render import render_to_stdout

SESSIONS_DIR = Path.home() / ".lea" / "sessions"

_UNSET = object()  # sentinel: distinguishes "caller omitted arg" from an explicit None


def _save_session(session_id: str, model: str, messages: list, usage: Usage):
    """Persist conversation to disk."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = SESSIONS_DIR / f"{session_id}.json"
    # Strip raw_part (non-serializable provider objects) from messages
    clean_messages = []
    for msg in messages:
        if msg["role"] == "assistant" and isinstance(msg["content"], list):
            clean_content = [
                {k: v for k, v in item.items() if k != "raw_part"}
                for item in msg["content"]
            ]
            clean_messages.append({"role": msg["role"], "content": clean_content})
        else:
            clean_messages.append(msg)
    data = {
        "id": session_id,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "usage": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens},
        "messages": clean_messages,
    }
    path.write_text(json.dumps(data, indent=2))


def _load_session(session_id: str | None) -> dict:
    """Load a session by ID, or the most recent one if ID is None."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if session_id:
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return json.loads(path.read_text())
    # Find most recent
    sessions = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        raise FileNotFoundError("No sessions found.")
    return json.loads(sessions[0].read_text())


def list_sessions() -> list[dict]:
    """Return a list of session summaries."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    sessions = sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    summaries = []
    for path in sessions[:20]:
        data = json.loads(path.read_text())
        # Extract the original task from the first user message
        task = data["messages"][0]["content"] if data["messages"] else ""
        if isinstance(task, str) and len(task) > 80:
            task = task[:80] + "..."
        summaries.append({
            "id": data["id"],
            "timestamp": data.get("timestamp", ""),
            "model": data.get("model", ""),
            "task": task,
            "turns": len([m for m in data["messages"] if m["role"] == "assistant"]),
        })
    return summaries


def run_events(config: LeaConfig, task: str, *, resume: str | bool = False):
    """Core loop as a generator: yields typed events, never prints.

    Yields SessionResumed?, then per turn: TurnStarted, AssistantTextDelta*,
    ToolCalled*, UsageUpdated, ToolResulted*, and finally Finished.
    """
    system = load_system_prompt(config.prompt_variant)
    model = config.model_name

    if resume:
        session = _load_session(resume if isinstance(resume, str) else None)
        messages = session["messages"]
        model = session.get("model", model)
        session_id = session["id"]
        total_usage = Usage(
            session.get("usage", {}).get("input_tokens", 0),
            session.get("usage", {}).get("output_tokens", 0),
        )
        if task:
            messages.append({"role": "user", "content": task})
        yield SessionResumed(session_id, len(messages))
    else:
        session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        messages = [{"role": "user", "content": task}]
        total_usage = Usage()

    total_cost = 0.0

    def transcript(turns: int) -> dict:
        clean = []
        for msg in messages:
            if msg["role"] == "assistant" and isinstance(msg["content"], list):
                clean.append({"role": "assistant", "content": [
                    {k: v for k, v in item.items() if k != "raw_part"}
                    for item in msg["content"]
                ]})
            else:
                clean.append(msg)
        return {
            "session_id": session_id,
            "model": model,
            "turns": turns,
            "usage": {"input_tokens": total_usage.input_tokens, "output_tokens": total_usage.output_tokens},
            "messages": clean,
        }

    turn = 0
    while True:
        turn += 1
        if config.max_turns and turn > config.max_turns:
            _save_session(session_id, model, messages, total_usage)
            yield Finished("max_turns", "Error: max turns reached without completing the proof.",
                           turn - 1, session_id, model, total_usage, total_cost, transcript(turn - 1))
            return

        yield TurnStarted(turn)

        assistant_parts = []
        current_text = ""
        tool_calls = []

        for event in stream(model, system, messages, TOOLS_SCHEMA, config.model_kwargs, streaming=config.stream):
            if isinstance(event, TextDelta):
                current_text += event.text
                yield AssistantTextDelta(event.text)
            elif isinstance(event, ToolCall):
                if current_text:
                    assistant_parts.append({"type": "text", "text": current_text})
                    current_text = ""
                yield ToolCalled(event.name, event.args)
                tool_calls.append({"name": event.name, "args": event.args, "id": None, "raw_part": event.raw_part})
            elif isinstance(event, _ToolMeta):
                if tool_calls:
                    tool_calls[-1]["id"] = event.tool_use_id
            elif isinstance(event, Done):
                total_usage.input_tokens += event.usage.input_tokens
                total_usage.output_tokens += event.usage.output_tokens
                total_cost += event.cost
                yield UsageUpdated(event.usage.input_tokens, event.usage.output_tokens, event.cost)

        if current_text:
            assistant_parts.append({"type": "text", "text": current_text})
        for tc in tool_calls:
            assistant_parts.append({
                "type": "tool_call",
                "name": tc["name"],
                "args": tc["args"],
                "id": tc["id"],
                "raw_part": tc.get("raw_part"),
            })
        messages.append({"role": "assistant", "content": assistant_parts})

        if not tool_calls:
            _save_session(session_id, model, messages, total_usage)
            text = "".join(p["text"] for p in assistant_parts if p["type"] == "text")
            yield Finished("completed", text or "(no response)", turn, session_id, model,
                           total_usage, total_cost, transcript(turn))
            return

        tool_results = []
        for tc in tool_calls:
            handler = TOOL_HANDLERS.get(tc["name"])
            if handler:
                try:
                    result = handler(tc["args"])
                except Exception as e:
                    result = f"Error: tool '{tc['name']}' raised {type(e).__name__}: {e}"
            else:
                result = f"Error: unknown tool '{tc['name']}'"

            preview = result[:200] + "..." if len(result) > 200 else result
            yield ToolResulted(tc["name"], result, preview)

            tool_result = {"type": "tool_result", "tool_name": tc["name"], "content": result}
            if tc["id"]:
                tool_result["tool_use_id"] = tc["id"]
                tool_result["tool_call_id"] = tc["id"]
            tool_results.append(tool_result)

        messages.append({"role": "user", "content": tool_results})
        _save_session(session_id, model, messages, total_usage)


def run(
    task: str,
    model: str | None = None,
    max_turns=_UNSET,
    provider: str | None = None,  # accepted for back-compat; LiteLLM routes by model name
    resume: str | bool = False,
    return_transcript: bool = False,
    prompt_variant: str | None = None,
) -> str | tuple[str, dict]:
    """Backward-compatible wrapper: run the agent and render to stdout.

    Builds a LeaConfig from defaults + any explicit overrides, drains the event
    stream through the default renderer, and returns the final text (and the
    transcript dict if return_transcript is True).
    """
    config = load_config(None)
    if model is not None:
        config.model_name = model
    if prompt_variant is not None:
        config.prompt_variant = prompt_variant
    if max_turns is not _UNSET:
        config.max_turns = max_turns

    text, transcript = render_to_stdout(run_events(config, task or "", resume=resume))
    return (text, transcript) if return_transcript else text
