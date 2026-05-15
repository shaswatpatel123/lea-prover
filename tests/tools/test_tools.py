"""Tools unit tests — exercises each tool through `build_handlers(env)`.

Uses a tempdir-backed LocalEnvironment for I/O tools. For `lean_check`
(which would need a real Lake project) we use a StubEnv that returns
canned `(rc, output)` values from `execute`.

Run:  python -m tests.tools.test_tools
Exits 0 if every check passes, 1 otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from lea.env import EnvironmentError
from lea.env.local import LocalEnvironment
from lea.tools import (
    TOOLS_SCHEMA,
    _to_rel,
    bash,
    build_handlers,
    edit_file,
    lean_check,
    read_file,
    search_mathlib,
    write_file,
)


_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))
        _failures.append(name)


def expect_raises(name: str, exc_type: type, fn) -> None:
    try:
        fn()
    except exc_type:
        print(f"  PASS  {name}")
        return
    except Exception as e:
        print(f"  FAIL  {name}  (raised {type(e).__name__}, expected {exc_type.__name__})")
        _failures.append(name)
        return
    print(f"  FAIL  {name}  (no exception raised)")
    _failures.append(name)


# ----------------------------------------------------------------------------
# Stub env for tests that don't want to spawn real subprocesses.
# ----------------------------------------------------------------------------

@dataclass
class StubEnv:
    project_root: str = "/work/stub"
    files: dict[str, bytes] = field(default_factory=dict)
    execute_responses: list[tuple[int, str]] = field(default_factory=list)
    execute_calls: list[tuple[str, str | None, int]] = field(default_factory=list)

    def execute(self, cmd, *, cwd=None, timeout=120):
        self.execute_calls.append((cmd, cwd, timeout))
        if self.execute_responses:
            return self.execute_responses.pop(0)
        return 0, ""

    def read_file(self, rel):
        return self.files[rel]

    def write_file(self, rel, data):
        self.files[rel] = data

    def exists(self, rel):
        return rel in self.files

    def snapshot(self):
        raise NotImplementedError

    def capture_modifications(self, snap, host_tar):
        raise NotImplementedError

    def cleanup(self):
        pass


# ----------------------------------------------------------------------------
# Path helper
# ----------------------------------------------------------------------------

def case_to_rel(env: LocalEnvironment, root: Path) -> None:
    print("[_to_rel]")
    check("relative path passes through normalized",
          _to_rel(env, "testbed/Main.lean") == "testbed/Main.lean")
    check("absolute path under project_root stripped",
          _to_rel(env, f"{env.project_root}/testbed/Main.lean") == "testbed/Main.lean")
    check("project_root itself maps to empty string",
          _to_rel(env, env.project_root) == "")
    expect_raises(
        "absolute path outside rejected",
        EnvironmentError,
        lambda: _to_rel(env, "/etc/passwd"),
    )
    expect_raises(
        "../ escape rejected",
        EnvironmentError,
        lambda: _to_rel(env, "../escape.lean"),
    )


# ----------------------------------------------------------------------------
# File tools
# ----------------------------------------------------------------------------

def case_write_read_round_trip(env: LocalEnvironment, root: Path) -> None:
    print("[write_file / read_file]")
    text = "import Mathlib\n\n-- unicode ∀ α ε ≤ 한국\ntheorem t : 1 = 1 := rfl\n"
    res = write_file(env, "testbed/Main.lean", text)
    check("write_file returns size message", res.startswith("Wrote ") and "testbed/Main.lean" in res,
          detail=f"got: {res!r}")
    check("write_file actually wrote", (root / "testbed" / "Main.lean").exists())

    got = read_file(env, "testbed/Main.lean")
    check("read_file round-trips UTF-8", got == text, detail=f"got {got!r}")

    # Line range slicing
    multi = "".join(f"line {i}\n" for i in range(1, 11))
    write_file(env, "many.txt", multi)
    sliced = read_file(env, "many.txt", start_line=3, end_line=5)
    check("read_file slice has header", sliced.startswith("# lines 3-5 of 10 in many.txt\n"),
          detail=sliced[:60])
    check("read_file slice has correct body",
          sliced.endswith("line 3\nline 4\nline 5\n"))

    missing = read_file(env, "nope/missing.lean")
    check("read_file on missing path returns error string",
          missing.startswith("Error:") and "does not exist" in missing)


def case_edit_file(env: LocalEnvironment, root: Path) -> None:
    print("[edit_file]")
    write_file(env, "edit.lean", "alpha\nbeta\ngamma\n")
    res = edit_file(env, "edit.lean", "beta", "BETA")
    check("edit_file unique replace returns OK", res == "OK")
    check("edit_file actually replaced", read_file(env, "edit.lean") == "alpha\nBETA\ngamma\n")

    res = edit_file(env, "edit.lean", "missing-text", "x")
    check("edit_file missing old_string returns error",
          res.startswith("Error:") and "not found" in res, detail=res)

    write_file(env, "dup.lean", "x = 1\nx = 1\n")
    res = edit_file(env, "dup.lean", "x = 1", "y = 1")
    check("edit_file ambiguous old_string returns error",
          res.startswith("Error:") and "appears 2 times" in res, detail=res)

    res = edit_file(env, "nope.lean", "a", "b")
    check("edit_file missing file returns error",
          res.startswith("Error:") and "does not exist" in res, detail=res)


# ----------------------------------------------------------------------------
# bash
# ----------------------------------------------------------------------------

def case_bash(env: LocalEnvironment, root: Path) -> None:
    print("[bash]")
    out = bash(env, "echo hello && echo world")
    check("bash returns combined output", "hello" in out and "world" in out, detail=out[:80])

    out = bash(env, "true")
    check("bash empty-output formats with exit code", out == "(no output, exit code 0)", detail=repr(out))

    # Truncation: write something > 10 KB
    out = bash(env, "yes hi | head -c 20000")
    check("bash truncates at 10 KB",
          len(out) <= 10100 and "... (truncated)" in out,
          detail=f"len={len(out)}")


# ----------------------------------------------------------------------------
# lean_check  (stubbed env)
# ----------------------------------------------------------------------------

def case_lean_check_stub() -> None:
    print("[lean_check / stubbed]")
    env = StubEnv()
    env.files["testbed/Main.lean"] = b"-- placeholder"

    env.execute_responses = [(0, "")]
    out = lean_check(env, "testbed/Main.lean")
    check("OK output when rc=0 and empty", out == "OK — no errors, no warnings.", detail=repr(out))
    assert env.execute_calls
    cmd, cwd, _ = env.execute_calls[-1]
    check("lean_check shells `lake env lean <rel>` with cwd=project_root",
          "lake env lean" in cmd and "testbed/Main.lean" in cmd and cwd == env.project_root,
          detail=f"cmd={cmd!r}, cwd={cwd!r}")

    env.execute_responses = [(1, "Main.lean:3:0: error: unknown identifier 'foo'")]
    out = lean_check(env, "testbed/Main.lean")
    check("non-zero rc returns the diagnostic output",
          "unknown identifier" in out and "error" in out, detail=out)

    env.execute_responses = [(-1, "\n[timeout after 120s]")]
    out = lean_check(env, "testbed/Main.lean")
    check("timeout returns lean-timeout marker",
          "timed out" in out.lower() or "timeout" in out.lower(), detail=out)

    out = lean_check(env, "testbed/Missing.lean")
    check("nonexistent file returns error string",
          out.startswith("Error:") and "does not exist" in out)


# ----------------------------------------------------------------------------
# search_mathlib  (tempdir with fake mathlib tree)
# ----------------------------------------------------------------------------

def case_search_mathlib(env: LocalEnvironment, root: Path) -> None:
    print("[search_mathlib]")
    # Build a fake .lake/packages/mathlib/Mathlib/... tree.
    mathlib = root / ".lake" / "packages" / "mathlib" / "Mathlib"
    (mathlib / "Algebra").mkdir(parents=True)
    (mathlib / "Algebra" / "Group.lean").write_text(
        "theorem mul_comm_universal (a b : G) : a * b = b * a := sorry\n"
        "lemma helper : True := trivial\n"
    )
    (mathlib / "Topology.lean").write_text(
        "def open_set : Set X := sorry\nlemma mul_comm_universal_again : 1 = 1 := rfl\n"
    )

    out = search_mathlib(env, "mul_comm_universal")
    check("search_mathlib finds matches across files",
          "Found" in out and "mul_comm_universal" in out, detail=out[:200])
    check("search_mathlib reports under Mathlib/ prefix",
          "Mathlib/Algebra/Group.lean" in out or "Mathlib/Topology.lean" in out,
          detail=out[:200])

    out = search_mathlib(env, "this-token-does-not-appear-anywhere-12345")
    check("search_mathlib no-match returns clean message",
          "No Mathlib results" in out, detail=out)


def case_search_mathlib_missing_mathlib() -> None:
    print("[search_mathlib / no mathlib]")
    with tempfile.TemporaryDirectory() as tmp:
        env = LocalEnvironment(tmp)
        out = search_mathlib(env, "anything")
        check("missing mathlib returns clear error",
              out.startswith("Error:") and "Mathlib not found" in out, detail=out)


# ----------------------------------------------------------------------------
# build_handlers wiring
# ----------------------------------------------------------------------------

def case_build_handlers(env: LocalEnvironment, root: Path) -> None:
    print("[build_handlers]")
    handlers = build_handlers(env)
    expected = {"bash", "read_file", "write_file", "edit_file", "lean_check", "search_mathlib"}
    check("handlers has all 6 tools", set(handlers.keys()) == expected,
          detail=f"got: {sorted(handlers.keys())}")

    # End-to-end dispatch shape matches what agent.py uses.
    res = handlers["write_file"]({"path": "h.txt", "content": "hi"})
    check("write_file via handler", "Wrote 2 bytes" in res, detail=res)
    res = handlers["read_file"]({"path": "h.txt"})
    check("read_file via handler", res == "hi", detail=repr(res))


def case_tools_schema_intact() -> None:
    print("[TOOLS_SCHEMA]")
    names = [t["name"] for t in TOOLS_SCHEMA]
    expected = ["read_file", "write_file", "edit_file", "lean_check", "bash", "search_mathlib"]
    check("TOOLS_SCHEMA contains the 6 expected tools (in expected order)",
          names == expected, detail=f"got: {names}")
    for tool in TOOLS_SCHEMA:
        check(f"schema for {tool['name']} has description + input_schema",
              "description" in tool and "input_schema" in tool, detail=str(tool.keys()))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="lea-tools-test-") as tmp:
        root = Path(tmp)
        env = LocalEnvironment(str(root))
        try:
            case_to_rel(env, root)
            case_write_read_round_trip(env, root)
            case_edit_file(env, root)
            case_bash(env, root)
            case_lean_check_stub()
            case_search_mathlib(env, root)
            case_search_mathlib_missing_mathlib()
            case_build_handlers(env, root)
            case_tools_schema_intact()
        except Exception:
            print("UNEXPECTED EXCEPTION:")
            traceback.print_exc()
            return 1

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s): {_failures}")
        return 1
    print("OK: all tools checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
