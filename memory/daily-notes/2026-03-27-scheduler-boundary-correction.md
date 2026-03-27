# Daily Note: Scheduler Boundary Correction

## Purpose

This note records a boundary correction made explicit on 2026-03-27.

The correction is not about code style.
It is about architecture ownership.

The question is:

- what is the core job of `harness-engineering`
- what is the core job of `supervisor`
- why it is wrong for `lib/scheduler.py` to interpret human intent and rewrite slice contracts

This note should be treated as a runtime architecture clarification.

## Core Project Truth

`harness-engineering` is not the product.

It is the execution shell around a small set of agents.

Its mission is:

- bounded context
- explicit role separation
- durable runtime state
- human escalation only at real decision gates
- required verification before work is considered done

This means the harness exists to keep the work loop running.
It does not exist to become the author of product semantics.

The core responsibility of the harness is orchestration.
The core responsibility of `supervisor` is dispatch and state transition.

From `README.md` and `AGENT.md`, the runtime truth is:

- `supervisor` is the only scheduler
- specialist agents own narrow scopes
- only `communication-agent` faces the human
- routine blockers should be handled inside the harness first
- active runtime state lives under `.harness/`
- work is not done until verification has actually passed

None of those statements say that `scheduler.py` should interpret human free text as planning semantics.
None of those statements say that `scheduler.py` should rewrite the current design contract.

## What `scheduler.py` Should Own

`lib/scheduler.py` should own runtime orchestration truth.

That includes:

- current mission and runtime state
- next-agent selection
- handoff construction
- question routing
- decision-gate opening and reply resumption
- worktree assignment and promotion sequencing
- verification gating at the workflow level
- round progression
- cleanup scheduling
- failure, waiting, and completion state transitions

This is already a large responsibility.
It is enough.

`scheduler.py` is the control shell.
It is not the policy author.
It is not the planner.
It is not the decision interpreter.

## What Went Wrong

The implementation allowed `lib/scheduler.py` to absorb logic that does not belong to the scheduler boundary.

The specific wrong turn was allowing `scheduler.py` to do both of these:

1. interpret human gate replies into structured decision meaning
2. rewrite the current slice contract from that interpreted meaning

The relevant functions are:

- `_parse_supervisor_choice`
- `_answer_constraints`
- `_supervisor_decision_from_answer`
- `_contract_for_supervisor_decision`

Those functions are not mere transport helpers.
They encode policy.

They effectively let the scheduler say:

- this reply means `continue`
- this reply means `replan`
- these lines are constraints
- this decision should mutate the current slice into a blocker slice
- this contract should now carry modified work items and acceptance criteria

That is a real architecture violation.

## Why This Is Architecturally Wrong

### 1. It turns orchestration into semantic authorship

The scheduler should know that a reply arrived.
The scheduler may know which gate it belongs to.
The scheduler may know which agent was blocked.

The scheduler should not become the component that decides what the reply means for planning truth.

Once it does that, the control shell starts authoring workflow semantics instead of transporting them.

### 2. It mixes transport and business meaning

The communication lane exists to move a human decision through the runtime safely.

That lane should handle:

- gate creation
- message display
- reply collection
- persistence
- routing back to the waiting loop

It should not force the scheduler to infer product-level meaning from natural language.

Interpreting "continue" versus "replan" is not runtime transport.
It is planning policy.

### 3. It collapses the boundary between `supervisor` and `design`

`design-agent` exists to turn the current goal into a concrete slice contract.

If `scheduler.py` can directly rewrite that contract based on a human reply, then the design boundary is weakened.

The scheduler stops being:

- the owner of route selection

and starts becoming:

- a hidden planner
- a hidden contract mutator

That is exactly the kind of role collapse the harness is supposed to prevent.

### 4. It gives free-text heuristics too much authority

The current parsing approach relies on heuristics over human text.

That is unsafe as architecture truth.

A scheduler can safely persist:

- raw reply text
- gate id
- sender
- timestamp
- optional structured fields if the UI submits them explicitly

A scheduler should not safely assume:

- the first line definitely encodes the final decision
- the remaining lines definitely mean constraints
- the resulting mutation is definitely the intended plan change

That inference layer is too semantic and too lossy to live inside the central scheduler.

### 5. It hard-codes planning semantics into the runtime core

`_contract_for_supervisor_decision` is especially problematic.

It embeds planning transformations into `scheduler.py`.

That means the scheduler is no longer only saying:

- who runs next
- what state is active
- whether the loop should wait or continue

It is also saying:

- what the next slice should become
- how acceptance criteria should be rewritten
- how verification expectations should be altered
- whether the system should generate a blocker slice

That is not scheduler behavior.
That is plan mutation behavior.

## Why This Is Wrong Even If the Program Can Technically Do It

A program can technically do many things that it should not own.

The question is not "can the code parse it".
The question is "which boundary should own the meaning".

The answer here is:

- the scheduler may transport and persist decisions
- the scheduler may route the loop after a decision exists
- the scheduler should not be the place where human language becomes planning truth

If structured decision interpretation is needed, it should live in a boundary that is explicitly about decision policy or planning policy.

If contract mutation is needed, it should live with the design/planning side, not inside the orchestration core.

## What The Scheduler Should Do Instead

The correct scheduler behavior is narrower.

After a human reply arrives, `scheduler.py` should do only this:

1. persist the reply as runtime evidence
2. associate it with the gate and blocked owner
3. clear waiting state
4. resume the correct lane
5. hand the reply to the component that actually owns its semantic interpretation

That semantic owner should be one of these two options.

### Option A: structured decision payload

The communication surface submits an explicit decision schema, for example:

- `decision_type`
- `selected_option`
- `constraints`
- `notes`

In this model:

- the scheduler stores and routes structured decision data
- no free-text heuristic parser is needed in `scheduler.py`

This keeps scheduler behavior deterministic.

### Option B: design-side interpretation

The scheduler stores the raw reply and resumes the planning side.

Then `design-agent` or a planning-policy module interprets:

- what the reply means
- whether the slice should continue
- whether a replan is needed
- how the contract should change

This preserves the idea that planning semantics belong with planning.

## Concrete Boundary Correction

The corrected ownership should be:

### `scheduler.py`

Owns:

- runtime state
- route selection
- gate lifecycle
- blocked/resume transitions
- worktree lifecycle
- audit-driven state progression
- cleanup scheduling

Does not own:

- human intent interpretation from free text
- contract rewriting from human reply meaning
- planning-policy heuristics

### `communication-agent`

Owns:

- human-facing presentation
- reply collection
- optional structured input capture

Does not own:

- route decisions
- direct task mutation

### planning boundary

Owns:

- decision interpretation when business meaning is involved
- contract mutation
- slice rewrite or blocker-slice generation

Does not own:

- runtime waiting state
- gate opening
- cleanup scheduling

## What Was Especially Misleading

The code path could make the scheduler appear "smart" because it kept the loop moving automatically.

That is not enough to justify the boundary violation.

A harness is good when it keeps the loop running while preserving role separation.
It is not good when it preserves momentum by silently stealing semantic ownership from other roles.

The implementation mistake was not merely that the file became large.

The deeper mistake was that the central scheduler was allowed to become the place where:

- human language
- planning policy
- contract mutation

were collapsed into one runtime core.

That is the wrong center of gravity for this project.

## Immediate Guidance For Future Refactors

When refactoring `lib/scheduler.py`, preserve this rule:

- move semantic interpretation away from the scheduler before optimizing helper shape

That means:

1. keep `scheduler.py` as orchestration truth
2. remove free-text decision parsing from scheduler-owned code
3. remove contract mutation logic from scheduler-owned code
4. let the scheduler carry decision artifacts, not author their planning meaning
5. let planning-owning code decide how a reply changes the next slice

## Locked Conclusion

The core of this project is not "a smart scheduler that understands and rewrites everything".

The core of this project is:

- a supervisor-centered runtime shell
- explicit specialist role separation
- durable state
- controlled human escalation
- verification-gated progress

Therefore:

- `lib/scheduler.py` should schedule
- `lib/scheduler.py` should not interpret human planning intent
- `lib/scheduler.py` should not rewrite slice contracts from human free text

That behavior is architecturally wrong even if it is technically possible.

This boundary should now be treated as corrected design truth for future work.
