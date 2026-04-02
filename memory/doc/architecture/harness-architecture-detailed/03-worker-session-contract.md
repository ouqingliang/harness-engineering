# Worker Session Contract

> Document type: detailed architecture design
> Purpose: define the shared session contract for all workers

## Contract Scope

This contract applies to all role-scoped workers in the target harness runtime.

## Design Idea

This contract exists to keep all workers interoperable without over-designing the public protocol.

- the public contract is intentionally small: shared states, `spawn`, `continue`, `terminate`, and a thin notification envelope
- `continue` means continue the same session whether the runtime is attaching to a live worker or restoring persisted context
- evidence should move as artifact references and runtime events, not as a larger conversational protocol
- details that only matter for recovery, transcript handling, or transport stay below this shared contract

## Shared Session States

The shared session states are:

- `running`
- `waiting`
- `completed`
- `failed`
- `killed`

These are the only shared session states in the public contract.

## Actions

The public session actions are:

- `spawn`
- `continue`
- `terminate`

`resume` is internal recovery only. If the runtime restores persisted state after an interruption, it must still present the interaction as `spawn` or `continue`.

## Task Notification

Worker-to-supervisor notifications use a thin structural envelope.

Required fields:

- `session`
- `status`
- `summary`

Optional fields:

- `result`
- `output-file`

Recommended shape:

```text
<task-notification>
session: exec-123
status: completed
summary: execution finished for the current slice
result: focused checks passed
output-file: .harness/artifacts/execution/exec-123/result.md
</task-notification>
```

The notification should stay short enough for the supervisor to consume without reinterpretation.

`status` should use the shared session state vocabulary.
