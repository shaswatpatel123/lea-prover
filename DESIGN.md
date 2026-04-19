# Lea — Design Document

A minimal Lean 4 theorem proving agent, inspired by [Pi](https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent).

## Philosophy

Lea follows Pi's ethos of radical minimalism: if we don't need it, we don't build it. The agent should be transparent, observable, and simple enough to understand in a single sitting.

- **Minimal tools**: the smallest set of tools that lets an LLM write and verify Lean proofs.
- **Full observability**: every tool call, result, and model response is visible. No hidden orchestration.
- **Trust over guardrails**: no permission prompts. The agent has full access to files and shell.
- **Simple prompts**: frontier models already know how to be coding agents. Keep the system prompt short.
- **Collaborator, not oracle**: Lea is a tool for mathematicians, not a replacement. Legibility, insight, and the ability to intervene matter as much as raw solve rate.

## Architecture

```
User task (CLI) → agent loop → tool calls → Lean compilation → repeat until proof compiles
```

### Tools (6)

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents |
| `write_file` | Create or overwrite a file |
| `edit_file` | Replace an exact substring in a file |
| `lean_check` | Compile a `.lean` file via `lake env lean`, return diagnostics |
| `search_mathlib` | Grep Mathlib source for lemma names / type patterns |
| `bash` | Run a shell command (for `exact?`, `apply?`, `lake build`, etc.) |

### Implemented features

1. **Streaming output** — all model output streams in real time. Every tool call and result is visible as it happens.
2. **Multi-provider support** — Gemini, Anthropic, OpenAI via a thin provider abstraction in `providers.py`. Auto-detected from model name or set with `-p`.
3. **No default turn limit** — the agent runs until the model stops calling tools. `--max-turns` available as an optional safety valve.
4. **Bash tool** — the agent can run arbitrary shell commands, enabling `exact?`, `apply?`, `grep`, `lake build`, etc.
5. **Project-level prompt customization** — drop a `lea.md` file in the workspace to append project-specific instructions to the system prompt.
6. **Session persistence** — full conversation history saved to `~/.lea/sessions/` after each run. Resume with `--resume`.
7. **Cost and token tracking** — cumulative input/output tokens and estimated cost printed at the end of each run.

### Evaluation

Eval harness at `eval/run_minif2f.py` runs Lea against the [miniF2F](https://github.com/yangky11/miniF2F-lean4) benchmark (488 competition-level problems). Per-problem transcripts with timestamps saved to `eval/results/`. Early results: ~84% on the validation split with Gemini 3.1 Pro.

---

## Limitations of the current design

Lea's single-loop architecture -- write proof, compile, read errors, fix, repeat -- works well on competition math where proofs are short (less than 50 lines). 

But my guess is that this breaks down on harder, graduate-level mathematics:

1. **No proof structure.** The agent tries to write the entire proof in one shot. For a theorem requiring intermediate lemmas (which is most real mathematics), it either produces an unmanageable monolith or gets lost.

2. **Blind retries.** When a proof attempt fails, the agent sees only compiler errors. It has no mechanism to step back and reconsider its strategy; it just edits the same broken proof. This leads to loops where the agent makes the same mistake repeatedly.

3. **No awareness of proof state.** The agent doesn't know what it needs to prove at each `sorry`. It guesses from error messages rather than inspecting the actual goal. Lean has tools for this (`exact?`, `apply?`, `#check`) but the current prompt does not guide the agent to use them.

4. **Single strategy.** Every problem gets the same approach: try simple tactics, if this fails then start searching Mathlib. There is no mechanism to try fundamentally different or involved proof strategies or to reflect upon failed attempts.

## Planned features

The next version of Lea would address these limitations with a **sketch–fill–reflect loop**, inspired by [DeltaProver](https://arxiv.org/html/2507.15225) (95.9% miniF2F) and [DeepSeek-Prover-V2](https://arxiv.org/html/2504.21801v1). The key ideas are:

- Break a proof into a chain of `have` statements with `sorry` (this is the sketch phase).
- Fill each `sorry` independently (this is the fill phase).
- If some `sorry`s can't be filled, reflect on why, and resketch (this is the reflect phase).

This maps onto Lean's `have` construct, which introduces intermediate results that subsequent steps can use. The decomposition is itself a valid (but incomplete) Lean proof, so Lean's type checker validates the proof structure even before the details are filled in.

### The loop

```
┌─────────────────────────────────────────────────┐
│                                                 │
│   1. SKETCH                                     │
│      Write a proof skeleton:                    │
│        have h1 : ... := sorry                   │
│        have h2 : ... := sorry                   │
│        exact combine h1 h2                      │
│      Compile to verify the skeleton type-checks │
│                                                 │
│   2. FILL                                       │
│      For each sorry, run a Lea proving loop:    │
│        - try simple tactics (norm_num, simp)    │
│        - use exact?, apply? via bash            │
│        - search Mathlib if needed               │
│      Each sorry is an independent sub-problem.  │
│      (Can run in parallel.)                     │
│                                                 │
│   3. CHECK                                      │
│      If all sorry's filled and proof compiles:  │
│        → DONE                                   │
│      If some sorry's remain:                    │
│        → continue to REFLECT                    │
│                                                 │
│   4. REFLECT                                    │
│      Feed back which subgoals failed and why.   │
│      Ask for a NEW sketch with a different      │
│      decomposition strategy.                    │
│        → go to 1                                │
│                                                 │
└─────────────────────────────────────────────────┘
```

### What changes in the codebase

The decompose-and-prove loop is a layer over the existing `run()` function with specialized prompts for each phase. The agent's minimalist tool set stays the same, but there are three new prompts:

- **Sketch prompt**: "You are writing a proof outline. Use `have` statements with `sorry` to express the structure. Do NOT fill in the details — only write the skeleton. The skeleton must compile (with sorry warnings, but no errors)."

- **Fill prompt**: "You are filling in a single `sorry` in an existing proof. The goal state is: `<goal>`. Try `exact?`, `apply?`, `simp`, `norm_num`. Do not modify anything outside this sorry."

- **Reflect prompt**: "The following subgoals could not be proved: `<list>`. Here are the errors. Analyze why the decomposition failed. Write a NEW proof skeleton with a different strategy."


### Why this stays minimal

- **No new tools**: same 6 tools, same provider abstraction.
- **No MCP or LSP**: the agent uses `exact?` and `apply?` via the existing `bash` tool.
- **Prompts do the work**: the three phases differ only in their system prompt, not in code.
- **Legible to humans**: the sketch is a readable proof outline. A mathematician can inspect it, approve it, or modify it before the fill phase runs.

### CLI and strategy selection

The CLI stays the same: `lea "prove XYZ"`. The model decides whether to decompose or prove directly, based on the theorem's complexity. Simple theorems get proved in one shot; hard theorems get sketched, filled, and reflected. Optional hooks for interactive use:

- `lea --sketch "task"` — produce the sketch and stop, so a mathematician can review or edit before filling.
- `lea --fill path/to/sketch.lean` — fill sorry's in an existing file.

### Observability

Every phase leaves artifacts on disk. For a problem `foo`, the workspace accumulates:

```
workspace/proofs/
  foo.lean                  # final proof (or latest attempt)
  foo.sketch.1.lean         # first sketch
  foo.sketch.2.lean         # re-sketch after reflection (if any)
  foo.reflect.1.md          # reflection: what failed and why
```

The sketch files are valid (incomplete) Lean since they compile with sorry warnings. The reflect files are natural-language analysis. A mathematician can read the sequence to understand what the agent tried and why it changed strategy. Session transcripts (in `~/.lea/sessions/`) capture the full conversation for each phase, including all tool calls and results.

### Evaluation plan

- Run on miniF2F with `prove_hard` and compare to single-loop `run()`.
- Run on [FormalQualBench](https://www.math.inc/formalqualbench) (23 graduate-level theorems) as the real target.
- Track pass rate, turns per problem, tokens per problem, number of re-sketches needed.
