"""Lea's six tools — the minimum surface area for Lean formalization.

Every tool routes its file I/O and command execution through an Environment.
Pass `env=LocalEnvironment(<lake_project_dir>)` for serial host runs, or
`env=DockerEnvironment(image, project_root)` for isolated parallel runs.

TOOLS_SCHEMA is unchanged — the model API does not care which env we use.
TOOL_HANDLERS is now a factory `build_handlers(env)` so each agent run binds
its own env without module-level state.
"""

import posixpath
import shlex
from typing import Callable

from .env import Environment, EnvironmentError


TOOLS_SCHEMA = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Optionally restrict to a line range (1-indexed, inclusive) to avoid pulling large files into context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to read."},
                "start_line": {"type": "integer", "description": "Optional 1-indexed first line to include."},
                "end_line": {"type": "integer", "description": "Optional 1-indexed last line to include (inclusive)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to write."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact substring in a file with new text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file to edit."},
                "old_string": {"type": "string", "description": "Exact text to find."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "lean_check",
        "description": "Compile a .lean file and return diagnostics (errors, warnings, goals). Uses `lake env lean` with the env's project as the Lake root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the .lean file to check."}
            },
            "required": ["path"],
        },
    },
    {
        "name": "bash",
        "description": "Run a shell command and return stdout + stderr. Use for `lake build`, git, file I/O outside the dedicated tools, etc. Do NOT use for Lean compilation (use `lean_check`) or Mathlib search (use `search_mathlib`).",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120).",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_mathlib",
        "description": "Search Mathlib for lemmas/theorems matching a query. Greps Mathlib source files inside the current project's `.lake/packages/mathlib/`.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — a lemma name fragment, type signature pattern, or keyword.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
]


# ----------------------------------------------------------------------------
# Path handling
# ----------------------------------------------------------------------------

def _to_rel(env: Environment, path: str) -> str:
    """Normalize `path` (relative or absolute) to a project-relative posix path.
    Rejects paths that escape `env.project_root`."""
    if path.startswith("/"):
        # Allow absolute paths that fall under project_root.
        if path == env.project_root:
            return ""
        prefix = env.project_root.rstrip("/") + "/"
        if not path.startswith(prefix):
            raise EnvironmentError(f"path outside project root: {path}")
        return path[len(prefix):]
    # Relative — but reject `..` escape just in case.
    norm = posixpath.normpath(path)
    if norm.startswith("..") or norm == "..":
        raise EnvironmentError(f"path outside project root: {path}")
    return norm


# ----------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------

def read_file(env: Environment, path: str, start_line: int | None = None, end_line: int | None = None) -> str:
    rel = _to_rel(env, path)
    if not env.exists(rel):
        return f"Error: {path} does not exist."
    text = env.read_file(rel).decode("utf-8", errors="replace")
    if start_line is None and end_line is None:
        return text
    lines = text.splitlines(keepends=True)
    s = max(0, (start_line or 1) - 1)
    e = end_line if end_line is not None else len(lines)
    sliced = "".join(lines[s:e])
    header = f"# lines {s + 1}-{min(e, len(lines))} of {len(lines)} in {rel}\n"
    return header + sliced


def write_file(env: Environment, path: str, content: str) -> str:
    rel = _to_rel(env, path)
    env.write_file(rel, content.encode("utf-8"))
    return f"Wrote {len(content)} bytes to {rel}"


def edit_file(env: Environment, path: str, old_string: str, new_string: str) -> str:
    rel = _to_rel(env, path)
    if not env.exists(rel):
        return f"Error: {path} does not exist."
    text = env.read_file(rel).decode("utf-8", errors="replace")
    count = text.count(old_string)
    if count == 0:
        return "Error: old_string not found in file."
    if count > 1:
        return f"Error: old_string appears {count} times. Provide more context to make it unique."
    env.write_file(rel, text.replace(old_string, new_string, 1).encode("utf-8"))
    return "OK"


def lean_check(env: Environment, path: str) -> str:
    rel = _to_rel(env, path)
    if not env.exists(rel):
        return f"Error: {path} does not exist."
    rc, output = env.execute(
        f"lake env lean {shlex.quote(rel)}",
        cwd=env.project_root,
        timeout=120,
    )
    output = output.strip()
    if rc == 0 and not output:
        return "OK — no errors, no warnings."
    if rc == -1 and "timeout" in output:
        return "Error: lean timed out after 120s."
    return output if output else f"Exit code {rc} (no output)."


def bash(env: Environment, command: str, timeout: int = 120) -> str:
    rc, output = env.execute(command, timeout=timeout)
    output = output.strip()
    if not output:
        return f"(no output, exit code {rc})"
    if len(output) > 10000:
        output = output[:10000] + "\n... (truncated)"
    return output


def search_mathlib(env: Environment, query: str, max_results: int = 10) -> str:
    """Grep the env's Mathlib (`.lake/packages/mathlib/Mathlib/`) for `query`.

    Strategy: find files that match, then pull up to two matching lines per
    file. Caps total lines returned at `max_results`. Returns a friendly
    'no results' message when nothing matches.
    """
    mathlib = ".lake/packages/mathlib/Mathlib"
    if not env.exists(mathlib):
        return f"Error: Mathlib not found at {mathlib} inside the env."

    q = shlex.quote(query)
    ml = shlex.quote(mathlib)
    # First pass: list files containing the query.
    rc, files_out = env.execute(
        f"grep -rl --include='*.lean' {q} {ml}",
        timeout=30,
    )
    if rc != 0 and not files_out.strip():
        return f"No Mathlib results for {query!r}."
    files = [f for f in files_out.strip().splitlines() if f][:max_results]
    if not files:
        return f"No Mathlib results for {query!r}."

    lines: list[str] = []
    for f in files:
        rc, hit_out = env.execute(
            f"grep -n {q} {shlex.quote(f)} | head -2",
            timeout=10,
        )
        for line in hit_out.strip().splitlines():
            short = f.split("Mathlib/", 1)[-1] if "Mathlib/" in f else f
            lines.append(f"  Mathlib/{short}:{line}")
            if len(lines) >= max_results:
                break
        if len(lines) >= max_results:
            break

    return f"Found {len(lines)} matches:\n" + "\n".join(lines)


# ----------------------------------------------------------------------------
# Handler factory — one per agent.run() call so handlers close over their env.
# ----------------------------------------------------------------------------

def build_handlers(env: Environment) -> dict[str, Callable]:
    return {
        "bash":           lambda a: bash(env, a["command"], a.get("timeout", 120)),
        "read_file":      lambda a: read_file(env, a["path"], a.get("start_line"), a.get("end_line")),
        "write_file":     lambda a: write_file(env, a["path"], a["content"]),
        "edit_file":      lambda a: edit_file(env, a["path"], a["old_string"], a["new_string"]),
        "lean_check":     lambda a: lean_check(env, a["path"]),
        "search_mathlib": lambda a: search_mathlib(env, a["query"], a.get("max_results", 10)),
    }
