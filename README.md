# Lea

A minimal Lean 4 theorem proving agent, inspired by [pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent).

Lea translates natural-language math statements into Lean 4 proofs that compile with zero errors and zero `sorry`s.

## Setup

Requires Python 3.13+ and [Lean 4](https://leanprover.github.io/lean4/doc/setup.html).

```bash
# Install
pip install -e .

# Build the Lean workspace (downloads Mathlib — takes a while the first time)
cd workspace && lake build

# Set your API key (at least one)
export GOOGLE_API_KEY=...     # for Gemini models
export ANTHROPIC_API_KEY=...  # for Claude models
export OPENAI_API_KEY=...     # for GPT/o-series models
```

## Usage

```bash
lea "Prove that the square root of 2 is irrational"
lea "Prove that for all natural numbers n, n + 0 = n"

# Use a different model
lea -m claude-sonnet-4-20250514 "Prove that 2 + 3 = 5"
lea -m gpt-4o "Prove that every prime greater than 2 is odd"

# Explicit provider (auto-detected from model name by default)
lea -p anthropic -m claude-opus-4-20250514 "..."

# Session management
lea --sessions                 # list recent sessions
lea --resume                   # resume most recent session
lea --resume 20260418-031200   # resume a specific session

# Safety valve
lea --max-turns 20 "..."       # limit turns (default: unlimited)
```

## Supported providers

| Provider | Models | Env var |
|----------|--------|---------|
| Gemini | `gemini-3.1-pro-preview`, `gemini-2.5-pro`, `gemini-2.5-flash`, ... | `GOOGLE_API_KEY` |
| Anthropic | `claude-opus-4-*`, `claude-sonnet-4-*`, ... | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o`, `o3`, `o4-mini`, ... | `OPENAI_API_KEY` |

Provider is auto-detected from the model name, or set explicitly with `-p`.

## How it works

Lea runs a simple loop:

1. Write a `.lean` file with a first-attempt proof using basic tactics (`norm_num`, `simp`, `omega`, `linarith`, `decide`)
2. Compile with `lean_check`
3. If it compiles — done. If not — read the errors, edit, retry.
4. If stuck, search Mathlib for relevant lemmas, or use `bash` to explore.

Six tools: `read_file`, `write_file`, `edit_file`, `lean_check`, `search_mathlib`, `bash`.

## Customization

Drop a `lea.md` file in your working directory or workspace root to add project-specific instructions to the system prompt (preferred tactics, import conventions, etc.).
