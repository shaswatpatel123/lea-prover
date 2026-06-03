"""Integration tests for MCP: real stdio round-trip + warn-and-continue.

Launches the stdio stub server (tests/mcp/_server.py) through MCPManager and
checks its tools register into the shared registry, are callable, and unregister
on stop. Also checks a server that fails to start is skipped (no raise). No
network, no node — just a Python subprocess speaking MCP over stdio.

Run:  uv run python -m tests.mcp.test_mcp
Exits 0 if every check passes, 1 otherwise.
"""

import sys
from pathlib import Path

from lea.mcp import MCPManager
from lea.registry import REGISTRY, build_toolset

_FAILURES: list[str] = []

SERVER = str(Path(__file__).resolve().parent / "_server.py")


def check(name: str, cond: bool) -> None:
    print(f"  ok   {name}" if cond else f"  FAIL {name}")
    if not cond:
        _FAILURES.append(name)


def test_stdio_roundtrip():
    mgr = MCPManager({"stub": {"command": sys.executable, "args": [SERVER]}})
    mgr.start()
    try:
        schemas, handlers = build_toolset(None)
        names = [s["name"] for s in schemas]
        check("stub__secret registered (namespaced)", "stub__secret" in names)
        check("stub__echo registered (namespaced)", "stub__echo" in names)
        check("secret tool call returns server value", handlers["stub__secret"]({}) == "MCP-SECRET-9")
        check("echo tool call passes args", handlers["stub__echo"]({"text": "hi"}) == "stub-echo: hi")
        echo_schema = next(s for s in schemas if s["name"] == "stub__echo")
        check("echo schema carries input_schema", "text" in echo_schema["input_schema"].get("properties", {}))
        check("echo schema carries description", bool(echo_schema["description"]))
    finally:
        mgr.stop()
    check("MCP tools unregistered after stop", not any(n.startswith("stub__") for n in REGISTRY))


def test_warn_and_continue_on_bad_server():
    mgr = MCPManager({"bad": {"command": "definitely-not-a-real-binary-xyz", "args": []}})
    try:
        mgr.start()  # must not raise — warn and continue
        check("start() did not raise on unstartable server", True)
        check("no tools from failed server", not any(n.startswith("bad__") for n in REGISTRY))
    finally:
        mgr.stop()


def test_no_servers_is_noop():
    mgr = MCPManager({})
    mgr.start()
    mgr.stop()
    check("empty manager start/stop is a no-op", True)


def main():
    print("mcp integration tests:")
    test_no_servers_is_noop()
    test_warn_and_continue_on_bad_server()
    test_stdio_roundtrip()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}): {', '.join(_FAILURES)}")
        sys.exit(1)
    print("All mcp tests passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
