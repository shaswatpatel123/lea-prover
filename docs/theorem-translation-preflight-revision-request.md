# Revision Request: Theorem Translation Preflight Robustness

## Summary

The theorem-translation approval tier can fail before the user ever sees an
approval request. The failure is not in the UI approval flow itself and not in
the downstream proof search loop. It is happening in the preflight step that
generates a top-level Lean theorem skeleton and requires that skeleton to
typecheck before emitting `approval_requested`.

The immediate symptom observed through the UI was:

```text
Error: theorem translation failed: RuntimeError: theorem translation failed to typecheck after 3 attempts.

Last candidate:
import Mathlib.Data.Nat.Basic
import Mathlib.Tactic

open Nat

theorem sum_of_first_n_odd_numbers (n : Nat) :
    (finset.range n).sum (lambda k, 2 * k + 1) = n * n := by sorry

Last diagnostics:
... error: unexpected token ','; expected '->', '=>'
```

The exact saved Lean file used `lambda` syntax rendered as Lean lambda syntax,
but the underlying issue is the same: the model produced invalid Lean 4 syntax
and an invalid namespace reference.

## Observed Failures

Two consecutive attempts for the same informal theorem failed during preflight:

```text
Prove that for every natural number n, the sum of the first n odd numbers is n squared.
```

Failure 1:

```lean
import Mathlib.Data.Nat.Basic

theorem sum_first_n_odd_numbers (n : Nat) :
    (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by
  sorry
```

Diagnostic:

```text
Unknown identifier `Finset.range`
```

This candidate used correct Lean 4 lambda syntax, but the import was too narrow
for this environment. `import Mathlib` would have avoided the problem.

Failure 2:

```lean
import Mathlib.Data.Nat.Basic
import Mathlib.Tactic

open Nat

theorem sum_of_first_n_odd_numbers (n : Nat) :
    (finset.range n).sum (lambda k, 2 * k + 1) = n * n := by
  sorry
```

Diagnostics:

```text
unexpected token ','; expected '->', '=>'
```

This candidate used Lean 3-style lambda syntax and lowercase `finset.range`.
Lean 4 expects `fun k => ...` or Lean 4 lambda syntax with `=>`, and the
namespace is `Finset`, not `finset`.

In both cases, the run ended with `turns: 0`, so the normal proof agent never
started.

## Current Control Flow

The relevant control flow is in `lea/agent.py`:

- `_THEOREM_TRANSLATION_PROMPT` asks the model to produce one Lean file with a
  theorem body of `by sorry`.
- `_checked_theorem_translation` extracts the Lean block, rewrites the first
  theorem body to `by sorry`, writes it under
  `workspace/proofs/.lea_proposals`, and calls `lean_check`.
- The function retries internally up to 3 times.
- If all 3 attempts contain Lean errors, it raises a `RuntimeError`.
- `run_events` catches that exception and yields a terminal
  `Finished("theorem_translation_failed", ...)` event before any
  `ApprovalRequested` event is emitted.

This means `permission_tier = "theorem_translation"` currently requires the
model to produce a syntactically valid and import-valid theorem declaration
before the user can review anything.

## Requested Changes

### 1. Strengthen the theorem-translation prompt

Revise `_THEOREM_TRANSLATION_PROMPT` so that it is explicit about Lean 4 syntax
and safe imports.

Recommended additions:

```text
- Use Lean 4 syntax only.
- Use `fun x => ...` or Lean 4 lambda syntax with `=>`; never use Lean 3
  lambda syntax with a comma.
- Lean namespaces are case-sensitive. Use `Finset.range`, not `finset.range`.
- Prefer `import Mathlib` unless there is a strong reason to use narrower
  imports.
- The returned file must pass `lean_check` with no errors. `sorry` warnings are
  acceptable in this preflight step.
```

Reasoning:

The theorem-translation stage is a guardrail, not a final proof optimizer. Its
first priority should be generating a reviewable, typechecking declaration.
Minimal imports are less important here than reliability.

### 2. Improve the retry repair prompt

When a preflight attempt fails, the correction prompt currently includes recent
diagnostics. It should also include the failed candidate code and more explicit
repair instructions.

Recommended repair message structure:

```text
The previous Lean theorem declaration did not typecheck.
Return a corrected complete Lean file only.

Requirements:
- Preserve the user's mathematical claim.
- Keep exactly one theorem or lemma.
- Use Lean 4 syntax only.
- Prefer `import Mathlib` if the error may be caused by missing imports.
- The body must be `by sorry`.

Previous candidate:
```lean
...
```

Diagnostics:
...
```

Reasoning:

Diagnostics alone can be under-specified. Including the failed candidate lets
the model make local repairs instead of regenerating from scratch and possibly
introducing a different error.

### 3. Log all failed preflight attempts

Preserve each failed candidate and diagnostic in the terminal error or in a
structured transcript/debug field.

Minimum acceptable change:

- Include all failed candidates and diagnostics in the raised
  `RuntimeError`, not only the last candidate.

Better change:

- Emit or store a structured proposal-attempt record containing:
  - attempt number
  - candidate code
  - lean_check output
  - whether it passed

Reasoning:

The current failure message hides whether the model improved, regressed, or
cycled. That makes it hard to diagnose prompt quality and model-specific
behavior.

### 4. Consider increasing retries from 3 to 5

Increase the internal retry budget for theorem-translation preflight from 3 to
5, or make it configurable.

Reasoning:

The preflight stage is cheap relative to downstream proof search. A small
increase in retries can prevent avoidable turn-0 failures, especially when the
first failure is a trivial import or syntax issue.

### 5. Optionally add safe syntax normalization

Consider a small normalization pass before `lean_check` for high-confidence
syntax mistakes:

- Replace lowercase `finset.` with `Finset.` only when it appears as a namespace
  prefix.
- Do not broadly rewrite arbitrary identifiers.
- Be cautious with lambda comma rewrites. A parser-aware or tightly scoped
  transformation is preferred over a global regex.

Reasoning:

Prompting and repair should be the primary fix. Normalization can help with
obvious, repeated model artifacts, but it must not silently change theorem
meaning.

## Expected Behavior After Revision

For the informal theorem:

```text
Prove that for every natural number n, the sum of the first n odd numbers is n squared.
```

The preflight should produce a checked skeleton similar to:

```lean
import Mathlib

theorem sum_of_first_n_odd_numbers (n : Nat) :
    (Finset.range n).sum (fun k => 2 * k + 1) = n * n := by
  sorry
```

Then the agent should emit `approval_requested`, allowing the user to accept or
reject the theorem statement. Only after acceptance should normal proof search
begin.

## Test Methodology

Add or update tests at three levels.

### Unit tests for prompt repair behavior

Mock the model stream so the first attempts return invalid declarations and the
later attempt returns a valid one.

Cases to cover:

- Attempt 1 uses `fun k => ...` but imports too narrowly and fails on
  `Finset.range`; attempt 2 switches to `import Mathlib` and passes.
- Attempt 1 uses Lean 3-style lambda syntax; attempt 2 repairs to Lean 4
  syntax and passes.
- All attempts fail; the final error includes enough candidate and diagnostic
  information to debug each failed attempt.

### Agent event tests

With `permission_tier = "theorem_translation"`:

- If preflight succeeds, `ApprovalRequested` is emitted before proof turns.
- If preflight fails after all retries, the run finishes with
  `theorem_translation_failed` and `turns: 0`.
- If a candidate is repaired successfully after one or more failed attempts,
  the run proceeds to `ApprovalRequested`, not terminal failure.

### Integration smoke test

Run the theorem:

```text
Prove that for every natural number n, the sum of the first n odd numbers is n squared.
```

Expected result:

- A proposal file is generated under `workspace/proofs/.lea_proposals`.
- `lean_check` returns no errors for the proposal. `sorry` warnings are allowed.
- The API stream emits `approval_requested`.
- After approval, the normal proof loop starts.

## Non-Goals

- Do not require the theorem-translation preflight to prove the theorem.
- Do not optimize for minimal imports in this approval stage.
- Do not weaken or simplify the user's mathematical claim to make preflight
  easier.
- Do not make the UI responsible for repairing invalid Lean declarations. The
  UI should only review checked theorem translations.

## Practical Workaround Until Fixed

Set:

```toml
permission_tier = "none"
```

This bypasses theorem-translation approval and restores the older behavior where
the main proof loop can write, check, and repair Lean files directly.
