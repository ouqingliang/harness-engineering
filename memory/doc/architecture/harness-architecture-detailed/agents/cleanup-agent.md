# Cleanup Agent

> Document type: detailed architecture design
> Purpose: define runtime hygiene, recovery support, and stale-state cleanup without bypassing the main work chain

## Role Purpose

The cleanup agent owns runtime hygiene and recovery support.

It keeps the harness resumable by trimming debris after verification, preserving useful evidence, and compressing transient state into durable memory.

## Owned Truths

The cleanup agent owns the truth about:

- what runtime debris exists
- what can be safely removed
- what should be preserved for verification or replay
- what recovery note the supervisor should see

It does not own the implementation truth for repo changes under test.

## Boundaries

The cleanup agent may operate on the supervisor-managed runtime namespace under `.harness/`, transient artifacts, and hygiene data.

It does not:

- bypass the design -> execution -> verification chain for repository changes
- repair product code as a cleanup shortcut
- delete evidence that verification still needs
- rewrite the approved contract
- turn maintenance into a hidden implementation lane

If a repo change is needed, cleanup reports it so the supervisor can route the work back into the normal chain.

## Inputs

The cleanup agent receives:

- a supervisor cleanup brief
- the current runtime state
- completed session artifacts
- stale or temporary files marked for review
- recovery context after interruption or round close

## Outputs

The cleanup agent produces:

- a cleanup report
- a list of removed or retained debris
- recovery notes when resume support is needed
- durable summaries that help the next round start cleanly

## Artifacts

Expected artifacts include:

- hygiene reports
- resumability notes
- stale-state findings
- references to preserved evidence

The cleanup agent should prefer compact, durable summaries over large transient files.

## Interaction Contract With Supervisor

The supervisor decides when cleanup runs, usually after verification acceptance, during recovery, or on a maintenance cadence.

The cleanup agent reports what was cleaned, what was preserved, and what still needs attention.

If cleanup discovers a repo problem, it does not fix the repo directly unless the supervisor routes a proper design or execution slice.

## Round Behavior

Cleanup rounds are short and bounded.

The agent may:

- prune temporary debris
- compress runtime state
- preserve verification evidence
- prepare recovery notes for the next session

The agent should leave the main work chain intact and ready for the next design, execution, or verification pass.

## Failure Handling

If cleanup cannot safely remove something, it keeps the item and reports why.

If recovery is incomplete, it reports the missing state instead of guessing.

If a cleanup action risks evidence loss, the agent stops and preserves the evidence.

If the runtime is unhealthy, the agent reports the condition to the supervisor rather than trying to self-heal the repo through a side path.

## Do Not Do

- do not delete durable evidence needed for verification
- do not remove state that the supervisor still needs to resume
- do not repair repo code as a cleanup shortcut
- do not bypass design -> execution -> verification for repo changes
- do not hide hygiene problems behind a successful cleanup report
- do not treat cleanup as a replacement for implementation work
