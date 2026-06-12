"""Unit tests for the agent core: run_events() generator + run() wrapper.

Monkeypatches providers.stream with a fake two-turn generator and the tool
handlers, so the whole loop runs with no network and no disk side effects.

Run:  uv run python -m tests.agent.test_run_events
Exits 0 if every check passes, 1 otherwise.
"""

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import lea.agent as agent
from lea.config import LeaConfig
from lea.registry import REGISTRY, Tool, register
from lea.providers import TextDelta, ToolCall, Done, _ToolMeta, Usage
from lea.events import (
    TurnStarted, AssistantTextDelta, ToolCalled, ToolResulted, ApprovalRequested,
    ApprovalResolved, UsageUpdated, Finished,
)

_FAILURES: list[str] = []
_ORIGINAL_PROPOSAL_FILE = agent._proposal_file


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
    agent._proposal_file = _ORIGINAL_PROPOSAL_FILE
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
    agent._proposal_file = _ORIGINAL_PROPOSAL_FILE
    return calls


def cfg(max_turns=None, tools=None, skills=None, narrate_tool_steps=False):
    return LeaConfig(model_name="gemini/test", model_kwargs={}, stream=True,
                     prompt_variant="default", max_turns=max_turns,
                     tools=tools, tool_modules=[], skills=skills or [],
                     narrate_tool_steps=narrate_tool_steps, permission_tier="none",
                     theorem_translation_max_retries=3,
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
    agent._proposal_file = _ORIGINAL_PROPOSAL_FILE
    return calls


def install_preflight_fake(proposals, check_for_code):
    calls = {"proposal_count": 0, "repair_messages": [], "tmpdir": tempfile.TemporaryDirectory()}

    def fake_stream(model, system, messages, tools, model_kwargs=None, streaming=True):
        if "theorem-translation review mode" in system:
            calls["proposal_count"] += 1
            if len(messages) > 1:
                calls["repair_messages"].append(messages[-1]["content"])
            index = min(calls["proposal_count"] - 1, len(proposals) - 1)
            yield TextDelta(proposals[index])
            yield Done(Usage(10, 5), 0.001)
        else:
            yield TextDelta("Done after approval.")
            yield Done(Usage(5, 5), 0.0005)

    def fake_lean_check(path):
        return check_for_code(Path(path).read_text())

    agent.stream = fake_stream
    agent._tools.lean_check = fake_lean_check
    agent._proposal_file = lambda session_id, candidate: Path(calls["tmpdir"].name) / f"{session_id}_{candidate}.lean"
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


def test_theorem_translation_repairs_missing_import_before_approval():
    calls = install_preflight_fake(
        [
            "```lean\nimport Mathlib.Data.Nat.Basic\n\n"
            "theorem sum_first_n_odd_numbers (n : Nat) :\n"
            "    (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by sorry\n```",
            "```lean\nimport Mathlib\n\n"
            "theorem sum_first_n_odd_numbers (n : Nat) :\n"
            "    (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by sorry\n```",
        ],
        lambda code: "error: Unknown identifier `Finset.range`" if "Mathlib.Data.Nat.Basic" in code else "warning: declaration uses 'sorry'",
    )
    gen = agent.run_events(cfg_approval(), "prove sum of odd numbers")
    events, approval = collect_until(gen, ApprovalRequested)
    check("missing import repaired before approval", calls["proposal_count"] == 2)
    check("repair prompt included previous candidate", "Mathlib.Data.Nat.Basic" in calls["repair_messages"][0])
    check("repair prompt included diagnostics", "Unknown identifier" in calls["repair_messages"][0])
    check("approval emitted after repair", isinstance(approval, ApprovalRequested))
    check("approval uses repaired import", "import Mathlib" in approval.lean_code)
    check("approval before first proof turn after repair", not any(isinstance(e, TurnStarted) for e in events))


def test_theorem_translation_repairs_lean3_lambda_before_approval():
    calls = install_preflight_fake(
        [
            "```lean\nimport Mathlib\n\n"
            "theorem sum_first_n_odd_numbers (n : Nat) :\n"
            "    (Finset.range n).sum (lambda k, 2 * k + 1) = n * n := by sorry\n```",
            "```lean\nimport Mathlib\n\n"
            "theorem sum_first_n_odd_numbers (n : Nat) :\n"
            "    (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by sorry\n```",
        ],
        lambda code: "error: unexpected token ','; expected '->', '=>'" if "lambda k," in code else "warning: declaration uses 'sorry'",
    )
    gen = agent.run_events(cfg_approval(), "prove sum of odd numbers")
    _, approval = collect_until(gen, ApprovalRequested)
    check("lean3 lambda repaired before approval", calls["proposal_count"] == 2)
    check("lambda repair prompt included candidate", "lambda k," in calls["repair_messages"][0])
    check("lambda repair prompt requires Lean 4", "Use Lean 4 syntax only" in calls["repair_messages"][0])
    check("approval uses Lean 4 lambda", "fun k =>" in approval.lean_code)


def test_theorem_translation_failure_reports_all_attempts():
    install_preflight_fake(
        [
            "```lean\nimport Mathlib\n\ntheorem bad_one : True := by sorry\n```",
            "```lean\nimport Mathlib\n\ntheorem bad_two : True := by sorry\n```",
            "```lean\nimport Mathlib\n\ntheorem bad_three : True := by sorry\n```",
        ],
        lambda code: f"error: failed check for {code.split('theorem ')[1].split(' ')[0]}",
    )
    events = list(agent.run_events(cfg_approval(), "prove a thing"))
    fin = events[-1]
    check("preflight all-fail reason", isinstance(fin, Finished) and fin.reason == "theorem_translation_failed")
    check("preflight all-fail turns zero", fin.turns == 0)
    usage_events = [e for e in events if isinstance(e, UsageUpdated)]
    check("preflight all-fail emits usage", usage_events == [UsageUpdated(30, 15, 0.003)])
    check("preflight all-fail finished usage counted", fin.usage == Usage(30, 15))
    check("preflight all-fail finished cost counted", abs(fin.cost - 0.003) < 1e-9)
    check("preflight all-fail includes attempt 1", "Attempt 1 candidate" in fin.text and "bad_one" in fin.text)
    check("preflight all-fail includes attempt 2", "Attempt 2 candidate" in fin.text and "bad_two" in fin.text)
    check("preflight all-fail includes attempt 3", "Attempt 3 candidate" in fin.text and "bad_three" in fin.text)


def test_theorem_translation_retry_config_honored():
    calls = install_preflight_fake(
        ["```lean\nimport Mathlib\n\ntheorem bad_one : True := by sorry\n```"],
        lambda code: "error: still invalid",
    )
    config = cfg_approval()
    config.theorem_translation_max_retries = 1
    events = list(agent.run_events(config, "prove a thing"))
    fin = events[-1]
    check("retry config used once", calls["proposal_count"] == 1)
    check("retry config reflected in error", "after 1 attempts" in fin.text)


def test_accepted_theorem_header_guard():
    install_approval_fake(guard_drift=True)
    gen = agent.run_events(cfg_approval(), "prove a thing")
    events, _ = collect_until(gen, ApprovalRequested)
    events.append(gen.send({"decision": "accept"}))
    events.extend(list(gen))
    guarded = [e for e in events if isinstance(e, ToolResulted)]
    check("header drift rejected by guarded write_file", guarded and "accepted top-level theorem" in guarded[0].content)


def cfg_interactive():
    c = cfg()
    c.prompt_variant = "interactive"
    c.permission_tier = "theorem_translation"
    return c


def install_interactive_fake(decision):
    """Fake stream for an interactive, resumed session.

    Routes three kinds of model call by their system prompt: the intent
    classifier, the theorem-translation preflight, and the main loop turn.
    """
    calls = {"loop_tool_names": None, "search_called": False, "preflight_called": False}

    def fake_stream(model, system, messages, tools, model_kwargs=None, streaming=True):
        if "FORMALIZE or ASSISTANT" in system:  # intent classifier
            yield TextDelta(decision)
            yield Done(Usage(3, 1), 0.0001)
            return
        if "theorem-translation review mode" in system:  # preflight (FORMALIZE only)
            calls["preflight_called"] = True
            yield TextDelta("```lean\nimport Mathlib\n\ntheorem t : True := by sorry\n```")
            yield Done(Usage(10, 5), 0.001)
            return
        # main agentic loop turn
        calls["loop_tool_names"] = [t.get("name") for t in (tools or [])]
        if decision == "ASSISTANT" and not calls["search_called"]:
            calls["search_called"] = True
            yield TextDelta("Let me look that up. ")
            yield ToolCall("search_mathlib", {"query": "even"})
            yield _ToolMeta("call_s")
            yield Done(Usage(8, 4), 0.0002)
            return
        yield TextDelta("Here is the explanation, in plain terms.")
        yield Done(Usage(6, 3), 0.0002)

    resumed_session = {
        "id": "sess-1",
        "model": "gemini/test",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "messages": [
            {"role": "user", "content": "prove that the sum of two evens is even"},
            {"role": "assistant", "content": [{"type": "text", "text": "Proved it."}]},
        ],
    }
    agent.stream = fake_stream
    agent._tools.search_mathlib = lambda *a, **k: "Found: Nat.even_add in Mathlib/Algebra/Parity.lean"
    agent._load_session = lambda session_id=None: dict(resumed_session)
    agent._save_session = lambda *a, **k: None
    agent.load_system_prompt = lambda variant, skills=None: f"SYS[{variant}]"
    agent._proposal_file = _ORIGINAL_PROPOSAL_FILE
    return calls


def test_interactive_assistant_turn_skips_preflight_and_keeps_tools():
    calls = install_interactive_fake("ASSISTANT")
    events = list(agent.run_events(cfg_interactive(), "explain this proof so a newbie gets it", resume="sess-1"))

    check("assistant turn: no approval/preflight", not any(isinstance(e, ApprovalRequested) for e in events))
    check("assistant turn: preflight never called", calls["preflight_called"] is False)
    fin = events[-1]
    check("assistant turn: benign 'assistant' reason", isinstance(fin, Finished) and fin.reason == "assistant")
    check("assistant turn: answered in prose", "plain terms" in fin.text)
    # The user-requested case: a lemma-lookup question can use search_mathlib.
    check("assistant turn: full toolset available", "search_mathlib" in (calls["loop_tool_names"] or []))
    check("assistant turn: search_mathlib actually called",
          any(isinstance(e, ToolCalled) and e.name == "search_mathlib" for e in events))


def test_interactive_formalize_turn_still_preflights():
    calls = install_interactive_fake("FORMALIZE")
    gen = agent.run_events(cfg_interactive(), "now prove that 2 + 2 = 4", resume="sess-1")
    events, approval = collect_until(gen, ApprovalRequested)

    check("formalize turn: preflight ran", calls["preflight_called"] is True)
    check("formalize turn: approval requested", isinstance(approval, ApprovalRequested))
    check("formalize turn: approval before any proof turn", not any(isinstance(e, TurnStarted) for e in events))


def test_text_only_history_serializes_for_provider():
    """Regression: assistant turns must round-trip through _to_openai_messages.

    A bare-string assistant content made _to_openai_messages iterate the string
    char-by-char and call .get on a char ('str' object has no attribute 'get').
    """
    from lea.providers import _to_openai_messages

    resumed_messages = [
        {"role": "user", "content": "prove that 2 + 2 = 4"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I'll write the proof."},
            {"type": "tool_call", "name": "write_file", "args": {"path": "p.lean"}, "id": "c1"},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_name": "write_file", "content": "ok", "tool_call_id": "c1"},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "It compiles."}]},
        {"role": "user", "content": "explain it for a newbie"},
    ]
    history = agent._text_only_history(resumed_messages)
    try:
        oai = _to_openai_messages("SYS", history)
        ok = True
    except Exception as exc:  # noqa: BLE001 - the regression we are guarding against
        ok = False
        print(f"    _to_openai_messages raised: {type(exc).__name__}: {exc}")
    check("text-only history serializes without error", ok)
    check("latest user message preserved", history[-1] == {"role": "user", "content": "explain it for a newbie"})
    check(
        "assistant turns use parts format",
        all(m["role"] != "assistant" or isinstance(m["content"], list) for m in history),
    )


def main():
    print("agent (run_events + run) tests:")
    test_run_events_sequence()
    test_max_turns()
    test_narrate_tool_steps_instruction()
    test_narrate_tool_steps_forces_text_before_silent_tool_call()
    test_run_wrapper_return_shape()
    test_theorem_translation_accept_continues()
    test_theorem_translation_reject_feedback_loops()
    test_theorem_translation_repairs_missing_import_before_approval()
    test_theorem_translation_repairs_lean3_lambda_before_approval()
    test_theorem_translation_failure_reports_all_attempts()
    test_theorem_translation_retry_config_honored()
    test_accepted_theorem_header_guard()
    test_interactive_assistant_turn_skips_preflight_and_keeps_tools()
    test_interactive_formalize_turn_still_preflights()
    test_text_only_history_serializes_for_provider()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}): {', '.join(_FAILURES)}")
        sys.exit(1)
    print("All agent tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
