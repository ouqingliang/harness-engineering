# Daily Note: Decision Agent Boundary Redesign

## Purpose

This note records a runtime architecture correction proposed on 2026-03-27.

The correction is:

- the existing `communication-agent` should be renamed and redefined as `decision-agent`
- the role that evaluates severity and decides whether a case must go to the human should live in that renamed `decision-agent`
- the human-facing communication surface should become transport and presentation, not orchestration policy

This note replaces one overloaded agent with one correctly named agent.

The goal is not to add one more clever layer.
The goal is to make ownership honest.

## Core Correction

The system currently mixes four different responsibilities across `scheduler`, `communication`, `runner_bridge`, and the app server:

- detect that a specialist is blocked
- decide whether the blocker is routine, important, or human-critical
- decide whether to escalate to the human
- collect and persist the human reply

Those are not the same job.

The corrected design should be:

- `supervisor` owns runtime orchestration truth
- the old `communication-agent` role is replaced by `decision-agent`
- `decision-agent` owns blocker assessment and escalation judgment
- the human-facing communication surface owns display and reply transport
- specialist agents ask questions and report blockers, but do not self-authorize human escalation

In short:

- `decision-agent` decides whether a blocker becomes a human decision point
- `supervisor` records and enforces the resulting runtime state transition
- `communication` delivers the question and the reply

## Why This Matches The Project Better

The project runtime truth already says:

- `supervisor` is the only scheduler
- `communication` is a side-channel
- specialist agents own narrow scopes
- human escalation should happen only at explicit decision points

That means the current problem is not that the runtime needs both a communication agent and a decision agent.
The current problem is that the thing named `communication-agent` is actually trying to do decision work.

So the correction is a rename plus a boundary correction:

- keep the decision work
- move it under the honest name `decision-agent`
- remove the fake impression that this role is just a relay

The previous shape was unstable because the code kept drifting toward one of two bad outcomes:

- `communication-agent` secretly becoming a policy owner while pretending to be a relay
- `scheduler` secretly becoming a semantic interpreter

Both are wrong for different reasons.

`communication` is too close to transport.
`scheduler` is too central to safely absorb free-text meaning and escalation policy.

`decision-agent` is the right name because that is already the work this role is trying to do.

## Role Model After The Redesign

### `supervisor`

Owns:

- mission and runtime state
- next-agent selection
- worktree and verification sequencing
- gate lifecycle as a runtime fact
- waiting, resuming, failing, and completing

Does not own:

- interpreting blocker severity
- deciding whether a blocker deserves human escalation
- interpreting free-text human intent into planning semantics

### `decision-agent`

Owns:

- classifying blocker severity
- distinguishing routine blockers from human-level decisions
- deciding whether the case can be auto-resolved, supervisor-resolved, or must go to human
- drafting the human-facing decision brief when escalation is required
- normalizing the reply into a decision artifact for downstream consumption

Does not own:

- runtime state mutation
- worktree control
- direct gate persistence
- direct app-server mutation

### Human-Facing Communication Surface

Owns:

- showing the pending decision brief
- accepting free-text human replies
- preserving the reply exactly as written
- exposing runtime status and inbox state

Does not own:

- deciding whether a gate exists
- deciding what severity means
- deciding whether the reply is sufficient
- deciding what runtime branch executes next

This is not an agent role.
This is runtime infrastructure.

### Specialist Agents

Own:

- reporting facts, uncertainty, blockers, and local tradeoffs

Do not own:

- deciding that human input is required as a runtime fact
- opening gates themselves

They may recommend escalation.
They may not enforce it.

## Main Runtime Flow After The Redesign

The corrected blocker flow should be:

1. A specialist agent reports a blocker, question, or ambiguity.
2. `supervisor` records that blocker as runtime evidence.
3. `supervisor` routes the blocker to `decision-agent`.
4. `decision-agent` returns one of a small set of decisions.
5. `supervisor` applies that decision to the runtime state.
6. If the decision requires human input, `supervisor` opens a gate and publishes the `decision-agent` brief through the communication surface.
7. The human replies in free text.
8. The communication surface stores the raw reply as evidence.
9. `supervisor` resumes `decision-agent` with the raw reply and the original blocker context.
10. `decision-agent` emits the normalized decision result.
11. `supervisor` routes execution, design, retry, replan, or failure handling based on that result.

This preserves free-text human authority without letting the transport layer decide the state machine.

## What The Decision Agent Should Emit

`decision-agent` should not emit ad hoc prose that every caller has to reinterpret.

It should emit a bounded decision artifact.

Recommended artifact shape:

```json
{
  "decision_status": "auto_answered | supervisor_continue | escalate_to_human | reply_interpreted | insufficient_reply | fatal_blocker",
  "severity": "low | medium | high | critical",
  "escalation_target": "none | supervisor | human",
  "summary": "short explanation of the blocker and judgment",
  "rationale": [
    "why this classification was chosen",
    "what risk or ambiguity prevents autonomous continuation"
  ],
  "human_brief": {
    "title": "optional human-facing title",
    "question": "what the human should decide",
    "context": "only the context the human needs",
    "options": [],
    "recommended_reply_shape": "free text"
  },
  "normalized_reply": {
    "raw_reply": "original human text if present",
    "interpreted_intent": "plain-language interpretation",
    "constraints": [],
    "open_questions": []
  },
  "next_action": {
    "type": "resume_specialist | resume_design | retry_execution | replan | wait_human | fail",
    "target_agent": "optional agent id",
    "notes": []
  }
}
```

The exact fields can change.
The important rule is that the result is an explicit artifact owned by `decision-agent`, not inferred later inside `scheduler.py`.

## Free Text Still Stays Free Text

Human replies should remain free text.

That is not the part that needs correction.

The correction is:

- free text should be preserved exactly
- the interpretation of that free text should belong to `decision-agent`
- the resulting runtime transition should belong to `supervisor`

This avoids the previous confusion where the system behaved as if:

- `store` could resolve the gate
- `app_server` could own escalation
- `scheduler.py` could parse the reply into planning meaning

Free-text authority is compatible with strict ownership.

## Required Code Boundary Changes

### 1. Remove Gate Policy From `RunnerBridge`

Target:

- [lib/runner_bridge.py](C:/Users/oql/OneDrive/Study/AIMA-refactor/harness-engineering/lib/runner_bridge.py)

Required change:

- delete the `decision_gate` special case from `_default_turn_executor()`
- stop calling `_default_turn_executor(turn)` for default report construction when a custom executor already ran
- keep `RunnerBridge` as a low-level turn execution bridge only

After this change, `RunnerBridge` may execute a turn.
It may not materialize human escalation policy.

### 2. Remove Runtime Control Endpoints From The App Server

Target:

- [runners/codex_app_server.py](C:/Users/oql/OneDrive/Study/AIMA-refactor/harness-engineering/runners/codex_app_server.py)

Required change:

- delete `POST /communication/gates`
- delete `POST /communication/messages`
- keep `/human/reply`
- keep read-only runtime inspection endpoints
- make the server a human inbox and monitor, not a generic control API

After this change, the app server is allowed to display pending decisions and collect human replies.
It is not allowed to create runtime facts directly.

### 3. Reduce `CommunicationStore` To Persistence

Target:

- [lib/communication_api.py](C:/Users/oql/OneDrive/Study/AIMA-refactor/harness-engineering/lib/communication_api.py)

Required change:

- stop letting `CommunicationStore` own workflow semantics
- convert `open_gate()` into a pure persistence helper that stores a gate record created by `supervisor`
- convert `reply_to_gate()` into reply recording, not workflow closure
- remove the fallback that creates `RunnerBridge` when `create_server()` has no bridge

After this change, the store records facts.
It does not decide them.

### 4. Replace `communication-agent` With `decision-agent`

New targets, suggested:

- `lib/decision_policy.py`
- `lib/decision_artifact.py`
- `lib/decision_agent.py`
- or `lib/decision_components/**` if the role grows quickly

Required change:

- rename the runtime role from `communication` to `decision`
- define the blocker input schema
- define the output decision artifact
- replace the old communication-agent spec with the new decision-agent spec
- route unresolved blockers through `decision-agent` before any human escalation

The important design point is not to create a second neighboring role.
The important point is to rename the existing role so the system stops lying about what it does.

### 5. Demote Communication To Surface Only

Current issue:

- the role name suggests human-facing relay
- the runtime behavior has drifted toward policy and gate ownership

Required change:

- remove `communication-agent` from the runtime agent lane
- keep `communication` only as the human-facing surface, store, and transport path
- let that surface render and relay approved human-facing prompts from `decision-agent`

There should not be both a `communication-agent` and a `decision-agent`.
After the redesign, only `decision-agent` remains as the specialist role.

## Recommended Runtime States

The runtime should distinguish these states clearly:

- `blocked_pending_decision_review`
- `waiting_decision_agent`
- `waiting_human_reply`
- `decision_reply_received`
- `decision_interpreted`

The old single-bucket idea of "some gate is open" is too coarse.

The runtime needs to know whether it is:

- waiting for policy triage
- waiting for a human
- waiting for interpretation of a received human reply

That split will remove a lot of hidden branching from `scheduler.py`.

## What Should Move Out Of `scheduler.py`

The following logic should leave `scheduler.py`:

- free-text reply parsing
- `continue / replan / constraints` extraction
- contract mutation based on human reply
- ad hoc reply sufficiency heuristics

Those should move into the new decision boundary.

`scheduler.py` should remain responsible for:

- opening the runtime wait state when `decision-agent` requests human escalation
- resuming the correct lane when a reply arrives
- passing raw human evidence to `decision-agent`
- applying the resulting normalized action

## How `decision-agent` And `design-agent` Should Relate

These two roles must stay separate.

`decision-agent` answers:

- how serious is this blocker
- can the system keep going autonomously
- is human judgment required
- what did the human reply mean at a runtime level

`design-agent` answers:

- given the accepted decision, what should the next slice or contract be

That means:

- `decision-agent` interprets intent and escalation
- `design-agent` rewrites plans and contracts

This prevents the old bug where the scheduler became a hidden planner.

## Migration Order

The implementation should happen in this order.

### Phase 1: Remove The Wrong Ownership

- strip gate-opening semantics from `RunnerBridge`
- strip gate-creation endpoints from the app server
- strip workflow closure semantics from `CommunicationStore`

This phase removes active architectural violations.

### Phase 2: Introduce Explicit Decision Ownership

- replace the old `communication-agent` spec with a `decision-agent` spec and handoff path
- add blocker artifact and decision artifact schemas
- add supervisor routing into and out of `decision-agent`

This phase replaces the misleading boundary with the correct one.

### Phase 3: Rewire Human Escalation

- let `decision-agent` draft the human-facing brief
- let `supervisor` publish the approved gate
- let the communication surface persist raw human replies
- let `decision-agent` interpret the reply

This phase restores end-to-end consistency.

### Phase 4: Simplify The Remaining Scheduler Core

- remove remaining free-text decision parsing from `scheduler.py`
- remove contract mutation helpers from the scheduler boundary
- keep only state-machine operations in the scheduler

This phase finishes the architectural cleanup already started.

## Verification Expectations

The redesign is only done when the following are true:

- `RunnerBridge` no longer opens gates
- the app server can no longer create gates directly
- `CommunicationStore` no longer resolves workflow state by itself
- `scheduler.py` no longer interprets human free text into `continue / replan`
- blocker triage always goes through `decision-agent`
- human replies remain stored as raw evidence
- downstream planning changes happen through design-side logic, not scheduler-side mutation

## Locked Direction

The direction locked by this note is:

- keep human replies as free text
- remove escalation policy from transport and scheduler internals
- replace `communication-agent` with `decision-agent` as the explicit owner of blocker severity and human-escalation judgment
- keep `supervisor` as runtime state-machine owner
- keep communication as human I/O surface, not an agent-level policy owner

This is the cleaner version of the intent that was already trying to emerge in the codebase.
