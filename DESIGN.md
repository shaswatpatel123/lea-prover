# Lea — Design Document

A minimal Lean 4 theorem proving agent, inspired by [Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent).

## Philosophy

Lea follows Pi's ethos of radical minimalism: if we don't need it, we don't build it. The agent should be transparent, observable, and simple enough to understand in a single sitting.

- **Minimal tools**: the smallest set of tools that lets an LLM write and verify Lean proofs.
- **Full observability**: every tool call, result, and model response is visible. No hidden orchestration.
- **Trust over guardrails**: no permission prompts. The agent has full access to files and shell.
- **Simple prompts**: frontier models already know how to be coding agents. Keep the system prompt short.

## Current State

### Architecture

```
User task (CLI) → agent loop → tool calls → Lean compilation → repeat until proof compiles
```

### Tools (5)

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents |
| `write_file` | Create or overwrite a file |
| `edit_file` | Replace an exact substring in a file |
| `lean_check` | Compile a `.lean` file via `lake env lean`, return diagnostics |
| `search_mathlib` | Grep Mathlib source for lemma names / type patterns |

### Agent loop (`agent.py`)

- Gemini-only, synchronous, blocking.
- 30-turn hard cap.
- Verbose mode (`-v`) prints tool calls and results.
- Non-verbose mode is silent until the final response.

### System prompt (`prompt.py`)

~400 tokens. Describes the workspace path, a 7-step workflow, style rules, and critical rules (stop on success, never invent lemma names, etc.).

### CLI (`cli.py`)

`lea "task"` or `echo "task" | lea`. Flags: `-m MODEL`, `--max-turns N`, `-v`.

---

## Planned Features

### 1. Streaming output :white_check_mark:

**Problem**: The agent blocks silently for minutes. In non-verbose mode, you see nothing until it finishes. Even in verbose mode, output only appears after each full model response.

**Design**: Always stream model output as it arrives — text tokens printed immediately, tool calls shown as they're invoked, tool results printed inline. Remove the verbose/non-verbose distinction; the agent should always be observable.

### 2. Multi-provider support :white_check_mark:

**Problem**: Hardcoded to Gemini (`google-genai`). Can't use Claude, GPT, etc.

**Design**: Introduce a thin provider abstraction in a new `providers.py` module. Each provider implements:
- `create_client(api_key) -> client`
- `generate(client, model, system, messages, tools) -> stream of events`

Event types: `text_delta`, `tool_call`, `tool_result`, `done`.

Provider is selected based on model name prefix or a `-p` flag. Supported providers:
- `gemini-*` → Google GenAI
- `claude-*` → Anthropic
- `gpt-*` / `o3*` → OpenAI

Dependencies (`anthropic`, `openai`) are optional — import only when the provider is selected.

### 3. Remove turn limit :white_check_mark:

**Problem**: The 30-turn hard cap can cut off proofs that are making progress. Pi explicitly has no step limit.

**Design**: Remove `MAX_TURNS` as a default. The agent runs until the model stops calling tools. Keep `--max-turns` as an optional safety valve (default: unlimited), but don't enforce it by default.

### 4. Bash tool :white_check_mark:

**Problem**: The agent can only interact with Lean through `lean_check` and `search_mathlib`. It can't run `lake build`, inspect `.lake` state, use `exact?` / `apply?` interactively, or do anything else in the shell.

**Design**: Add a `bash` tool:
- `command` (string): the shell command to run.
- `timeout` (int, optional): timeout in seconds, default 120.
- Returns stdout + stderr, truncated to 10,000 chars.
- Synchronous execution, no background processes.

This subsumes `search_mathlib` — the agent can just `grep` Mathlib directly. Keep `search_mathlib` for now as a convenience, but it's no longer essential.

### 5. Project-level prompt customization (`lea.md`) :white_check_mark:

**Problem**: System prompt is hardcoded in `prompt.py`. Can't customize strategy per-project without editing source.

**Design**: On startup, look for a `lea.md` file in the workspace root (or current directory). If found, append its contents to the system prompt. This lets users add project-specific rules, preferred tactics, import conventions, etc.

Load order: base system prompt → `lea.md` (if present).

### 6. Session persistence :white_check_mark:

**Problem**: All context is lost when the agent exits. Can't resume a failed proof attempt.

**Design**: Save the full conversation history (messages + tool results) to a JSON file after each run. Store in `~/.lea/sessions/` with timestamp-based filenames.

CLI additions:
- `lea --resume` — continue the most recent session.
- `lea --resume SESSION_ID` — continue a specific session.
- `lea --sessions` — list past sessions.

### 7. Cost and token tracking :white_check_mark:

**Problem**: No visibility into how many tokens or dollars a proof attempt costs.

**Design**: After each model response, extract token counts from the API response metadata. Track cumulative input/output tokens and estimated cost. Print a summary line at the end of each run:

```
✓ Proof complete. 3 turns, 12,847 tokens ($0.04)
```

Token counts come from the API response; cost is estimated from a simple per-model price table.
