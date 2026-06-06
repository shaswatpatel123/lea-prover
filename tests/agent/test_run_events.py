"""Unit tests for the agent core: run_events() generator + run() wrapper.

Monkeypatches providers.stream with a fake two-turn generator and the tool
handlers, so the whole loop runs with no network and no disk side effects.

Run:  uv run python -m tests.agent.test_run_events
Exits 0 if every check passes, 1 otherwise.
"""

import io
import sys
from contextlib import redirect_stdout

import lea.agent as agent
from lea.config import LeaConfig
from lea.registry import REGISTRY, Tool, register
from lea.providers import TextDelta, ToolCall, Done, _ToolMeta, Usage
from lea.events import (
    TurnStarted, AssistantTextDelta, ToolCalled, ToolResulted, ApprovalRequested,
    ApprovalResolved, UsageUpdated, Finished,
)

_FAILURES: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  ok   {name}" if cond else f"  FAIL {name}")
    if not cond:
        _FAILURES.append(name)


def install_fakes():
    """Patch the agent's collaborators; returns a fresh two-turn fake stream."""
    calls = {"n": 0, "systems": []}

    def fake_stream(model, system, messages, tools, model_kwargs=None, streaming=True):
        calls["n"] += 1
        calls["systems"].append(system)
        if calls["n"] == 1:
            yield TextDelta("Let me check. ")
            yield ToolCall("echo", {"x": 1})
            yield _ToolMeta("call_1")
            yield Done(Usage(100, 40), 0.003)
        else:
            yield TextDelta("All done.")
            yield Done(Usage(20, 10), 0.001)

    agent.stream = fake_stream
    # The loop dispatches through the registry now, so register a real "echo"
    # tool (guarded — install_fakes runs per test) instead of patching a global.
    if "echo" not in REGISTRY:
        register(Tool(
            name="echo",
            schema={"name": "echo", "description": "echo args", "input_schema": {"type": "object"}},
            handler=lambda a: "echoed:" + str(a),
        ))
    agent._save_session = lambda *a, **k: None
    agent.load_system_prompt = lambda variant, skills=None: "SYS"
    return calls


def install_silent_tool_fake():
    calls = {"n": 0, "systems": []}

    def fake_stream(model, system, messages, tools, model_kwargs=None, streaming=True):
        calls["n"] += 1
        calls["systems"].append(system)
        if not tools:
            yield TextDelta("I will explain the proof move before using the tool.")
            yield Done(Usage(5, 7), 0.0001)
        elif calls["n"] == 1:
            yield ToolCall("echo", {"x": 1})
            yield _ToolMeta("call_1")
            yield Done(Usage(100, 40), 0.003)
        else:
            yield TextDelta("All done.")
            yield Done(Usage(20, 10), 0.001)

    agent.stream = fake_stream
    if "echo" not in REGISTRY:
        register(Tool(
            name="echo",
            schema={"name": "echo", "description": "echo args", "input_schema": {"type": "object"}},
            handler=lambda a: "echoed:" + str(a),
        ))
    agent._save_session = lambda *a, **k: None
    agent.load_system_prompt = lambda variant, skills=None: "SYS"
    return calls


def cfg(max_turns=None, tools=None, skills=None, narrate_tool_steps=False):
    return LeaConfig(model_name="gemini/test", model_kwargs={}, stream=True,
                     prompt_variant="default", max_turns=max_turns,
                     tools=tools, tool_modules=[], skills=skills or [],
                     narrate_tool_steps=narrate_tool_steps, permission_tier="none",
                     mcp_servers={})


def cfg_approval():
    c = cfg()
    c.permission_tier = "theorem_translation"
    return c


def install_approval_fake(*, guard_drift=False):
    calls = {"n": 0, "proposal_count": 0, "guard_called": False}

    def fake_stream(model, system, messages, tools, model_kwargs=None, streaming=True):
        calls["n"] += 1
        if "theorem-translation review mode" in system:
            calls["proposal_count"] += 1
            name = "approved_theorem"
            prop = "True" if calls["proposal_count"] == 1 else "2 + 2 = 4"
            yield TextDelta(f"```lean\nimport Mathlib\n\ntheorem {name} : {prop} := by sorry\n```")
            yield Done(Usage(10, 5), 0.001)
        elif guard_drift and not calls["guard_called"]:
            calls["guard_called"] = True
            yield ToolCall("write_file", {
                "path": "workspace/proofs/GuardDrift.lean",
                "content": "import Mathlib\n\ntheorem approved_theorem : False := by sorry\n",
            })
            yield _ToolMeta("call_guard")
            yield Done(Usage(20, 10), 0.002)
        elif guard_drift:
            yield TextDelta("Done after guard.")
            yield Done(Usage(5, 5), 0.0005)
        elif calls["n"] <= 3:
            yield TextDelta("Proving now.")
            yield ToolCall("echo", {"x": 2})
            yield _ToolMeta("call_2")
            yield Done(Usage(20, 10), 0.002)
        else:
            yield TextDelta("All done after approval.")
            yield Done(Usage(5, 5), 0.0005)

    agent.stream = fake_stream
    agent._tools.lean_check = lambda path: "warning: declaration uses 'sorry'"
    if "echo" not in REGISTRY:
        register(Tool(
            name="echo",
            schema={"name": "echo", "description": "echo args", "input_schema": {"type": "object"}},
            handler=lambda a: "echoed:" + str(a),
        ))
    agent._save_session = lambda *a, **k: None
    agent.load_system_prompt = lambda variant, skills=None: "SYS"
    return calls


def collect_until(gen, event_type):
    events = []
    while True:
        ev = next(gen)
        events.append(ev)
        if isinstance(ev, event_type):
            return events, ev


def test_run_events_sequence():
    install_fakes()
    events = list(agent.run_events(cfg(), "prove it"))
    types = [type(e).__name__ for e in events]
    expected_types = [
        "TurnStarted", "AssistantTextDelta", "ToolCalled", "UsageUpdated", "ToolResulted",
        "TurnStarted", "AssistantTextDelta", "UsageUpdated", "Finished",
    ]
    check("event order", types == expected_types)

    by = {t: [e for e in events if type(e).__name__ == t] for t in set(types)}
    check("turn-1 ToolCalled echo", by["ToolCalled"][0] == ToolCalled("echo", {"x": 1}))
    check("turn-1 UsageUpdated cost", by["UsageUpdated"][0] == UsageUpdated(100, 40, 0.003))
    check("ToolResulted content", by["ToolResulted"][0].content == "echoed:{'x': 1}")

    fin = events[-1]
    check("Finished.reason completed", fin.reason == "completed")
    check("Finished.text", fin.text == "All done.")
    check("Finished.turns", fin.turns == 2)
    check("Finished cumulative usage", fin.usage == Usage(120, 50))
    check("Finished cumulative cost", abs(fin.cost - 0.004) < 1e-9)
    check("transcript turns", fin.transcript["turns"] == 2)
    check("transcript has messages", len(fin.transcript["messages"]) >= 3)


def test_max_turns():
    install_fakes()
    events = list(agent.run_events(cfg(max_turns=1), "prove it"))
    fin = events[-1]
    check("max_turns Finished reason", fin.reason == "max_turns")
    check("max_turns Finished turns", fin.turns == 1)


def test_narrate_tool_steps_instruction():
    calls = install_fakes()
    list(agent.run_events(cfg(narrate_tool_steps=True), "prove it"))
    check("narration instruction added", "first write a concise progress" in calls["systems"][0])

    calls = install_fakes()
    list(agent.run_events(cfg(narrate_tool_steps=False), "prove it"))
    check("narration instruction omitted by default", "first write a concise progress" not in calls["systems"][0])


def test_narrate_tool_steps_forces_text_before_silent_tool_call():
    install_silent_tool_fake()
    events = list(agent.run_events(cfg(narrate_tool_steps=True), "prove it"))
    types = [type(e).__name__ for e in events]
    text_index = types.index("AssistantTextDelta")
    tool_index = types.index("ToolCalled")
    check("forced narration before tool call", text_index < tool_index)

    fin = events[-1]
    first_assistant = fin.transcript["messages"][1]
    check(
        "forced narration persisted before tool call",
        first_assistant["content"][0] == {
            "type": "text",
            "text": "I will explain the proof move before using the tool.",
        },
    )
    check("forced narration usage included", fin.usage == Usage(125, 57))


def test_run_wrapper_return_shape():
    install_fakes()
    buf = io.StringIO()
    with redirect_stdout(buf):
        out = agent.run("prove it", model="gemini/test", return_transcript=True)
    check("run() returns a 2-tuple", isinstance(out, tuple) and len(out) == 2)
    text, transcript = out
    check("run() text", text == "All done.")
    check("run() transcript turns", transcript["turns"] == 2)
    check("run() rendered to stdout", "--- turn 1 ---" in buf.getvalue())

    install_fakes()
    with redirect_stdout(io.StringIO()):
        out2 = agent.run("prove it", model="gemini/test")
    check("run() without transcript returns str", isinstance(out2, str) and out2 == "All done.")


def test_theorem_translation_accept_continues():
    install_approval_fake()
    gen = agent.run_events(cfg_approval(), "prove a thing")
    events, approval = collect_until(gen, ApprovalRequested)
    check("approval requested before first turn", not any(isinstance(e, TurnStarted) for e in events))
    check("approval has checked Lean code", "theorem approved_theorem" in approval.lean_code)

    events.append(gen.send({"decision": "accept"}))
    events.extend(list(gen))
    check("approval resolved accept", any(isinstance(e, ApprovalResolved) and e.decision == "accept" for e in events))
    check("run continues after accept", any(isinstance(e, TurnStarted) for e in events))
    check("finished after approval", isinstance(events[-1], Finished) and events[-1].text == "All done after approval.")


def test_theorem_translation_reject_feedback_loops():
    calls = install_approval_fake()
    gen = agent.run_events(cfg_approval(), "prove a thing")
    events, first = collect_until(gen, ApprovalRequested)
    events.append(gen.send({"decision": "reject", "feedback": "Make it arithmetic instead of trivial."}))
    more, second = collect_until(gen, ApprovalRequested)
    events.extend(more)
    check("second candidate requested after rejection", second.candidate == 2)
    check("proposal generated twice", calls["proposal_count"] == 2)

    events.append(gen.send({"decision": "accept"}))
    events.extend(list(gen))
    check("reject then accept finishes", isinstance(events[-1], Finished))


def test_accepted_theorem_header_guard():
    install_approval_fake(guard_drift=True)
    gen = agent.run_events(cfg_approval(), "prove a thing")
    events, _ = collect_until(gen, ApprovalRequested)
    events.append(gen.send({"decision": "accept"}))
    events.extend(list(gen))
    guarded = [e for e in events if isinstance(e, ToolResulted)]
    check("header drift rejected by guarded write_file", guarded and "accepted top-level theorem" in guarded[0].content)


def main():
    print("agent (run_events + run) tests:")
    test_run_events_sequence()
    test_max_turns()
    test_narrate_tool_steps_instruction()
    test_narrate_tool_steps_forces_text_before_silent_tool_call()
    test_run_wrapper_return_shape()
    test_theorem_translation_accept_continues()
    test_theorem_translation_reject_feedback_loops()
    test_accepted_theorem_header_guard()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}): {', '.join(_FAILURES)}")
        sys.exit(1)
    print("All agent tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
