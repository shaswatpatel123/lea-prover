# Theorem Translation Approval UI Handoff

Lea now supports a tier-two permission mode where the agent pauses before proof search, asks the user to approve the proposed top-level Lean theorem translation, and resumes only after the user accepts it. If the user rejects the translation, the UI must collect natural-language feedback and send it back so Lea can generate a revised candidate.

## Enable The Feature

Set the run config overlay:

```json
{
  "agent": {
    "permission_tier": "theorem_translation"
  }
}
```

Supported values:

- `none`: default; no approval prompts.
- `theorem_translation`: pause for approval of the checked top-level theorem translation.

`stepwise` is reserved for a future tier-three mode and is currently rejected by config validation.

## Run Status

`GET /v1/runs/{run_id}` can now return:

```json
{
  "run_id": "run_...",
  "status": "paused",
  "model": "...",
  "result": null,
  "pending_approval": {
    "type": "approval_requested",
    "approval_id": "...",
    "tier": "theorem_translation",
    "candidate": 1,
    "lean_code": "import Mathlib\n\ntheorem ... := by sorry",
    "theorem_name": "...",
    "check_result": "..."
  },
  "created_at": "...",
  "finished_at": null
}
```

The UI should treat `paused` as an active run waiting for user input, not as terminal.

## SSE Events

The existing run stream at:

```text
GET /v1/runs/{run_id}/events
```

now includes two non-terminal event types.

### `approval_requested`

```json
{
  "type": "approval_requested",
  "approval_id": "20260606-...-theorem-translation-1",
  "tier": "theorem_translation",
  "candidate": 1,
  "lean_code": "import Mathlib\n\ntheorem ... := by sorry",
  "theorem_name": "...",
  "check_result": "warning: declaration uses 'sorry'",
  "seq": 3,
  "schema_version": "1"
}
```

Render this as a review checkpoint. Show the `lean_code` prominently, and optionally show `theorem_name`, `candidate`, and `check_result`.

### `approval_resolved`

```json
{
  "type": "approval_resolved",
  "approval_id": "...",
  "decision": "accept",
  "feedback": null,
  "seq": 4,
  "schema_version": "1"
}
```

This confirms that Lea received the user decision. After this event, the run continues normally. If the decision was `reject`, expect another `approval_requested` event for a later candidate.

## Approval Endpoint

Send the user decision to:

```text
POST /v1/runs/{run_id}/approvals/{approval_id}
```

Accept body:

```json
{
  "decision": "accept"
}
```

Reject body:

```json
{
  "decision": "reject",
  "feedback": "The theorem should quantify over all natural numbers, not integers."
}
```

Rules:

- `decision` must be `accept` or `reject`.
- `feedback` is required for `reject`.
- The endpoint returns `409` if there is no matching pending approval.
- The endpoint returns `422` for invalid decisions or missing rejection feedback.
- Cancel still works while paused via `POST /v1/runs/{run_id}/cancel`.

## Recommended UI Flow

1. Start the run with `permission_tier: "theorem_translation"`.
2. Subscribe to `/events` as usual.
3. When `approval_requested` arrives, show a modal or review panel.
4. Provide two actions:
   - Accept: call the approval endpoint with `{ "decision": "accept" }`.
   - Reject: require a feedback text field, then call the endpoint with `{ "decision": "reject", "feedback": "..." }`.
5. Keep the run visible as `paused` while waiting for the user.
6. After accept, return to the normal proof-progress UI.
7. After reject, keep listening for the next `approval_requested` candidate.

## Important Behavior

- The approved item is a checked Lean theorem skeleton using `by sorry`; it is not a finished proof.
- Lea uses the accepted theorem declaration as an immutable top-level target during proof search.
- If the proving model later tries to alter the accepted theorem statement, Lea’s tool layer returns an error to the model rather than silently allowing drift.
- Proposal generation may internally retry invalid Lean translations before presenting anything to the user.

