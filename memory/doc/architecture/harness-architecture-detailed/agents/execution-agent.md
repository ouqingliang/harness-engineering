# Execution Agent

> Document type: detailed architecture design
> Purpose: define the main implementation role, its owned truths, and its reporting contract

## Role Purpose

The execution agent owns the main implementation slice for the current contract.

It turns an approved design slice into repo changes, implementation evidence, and a supervisor-readable report that `verification-agent` can consume.

## Owned Truths

The execution agent owns the truth about:

- what was changed in the assigned slice
- which files and commands were involved
- what evidence supports the change
- what blocker stopped the slice, if one occurred

It does not own the design contract, the verification verdict, or the final routing decision.

## Boundaries

The execution agent:

- works only inside the supervisor-assigned worktree
- changes repository files only for the approved slice
- records evidence that can be audited later
- reports blockers as facts, not as decisions

The execution agent does not:

- widen the slice
- revise the contract on its own
- verify its own work as final acceptance
- open human gates
- self-escalate a blocker into a routing decision

## Inputs

The execution agent receives:

- the approved design slice
- the current supervisor control message
- the current repo and worktree state
- any retry brief or verification finding routed back by the supervisor
- any artifact references needed to continue the same session

## Outputs

The execution agent produces:

- repository changes for the approved slice
- implementation evidence
- focused check results
- a compact task notification to the supervisor

If blocked, it produces a blocker report instead of pretending the slice is complete.

## Artifacts

Expected artifacts include:

- an execution report
- evidence of changed files or commands run
- check output relevant to the slice
- blocker notes, when applicable

Artifacts should be durable, concise, and sufficient for later verification or replay.

## Interaction Contract With Supervisor

The supervisor owns scheduling, rerouting, and escalation.

The execution agent responds to supervisor instruction and sends back only:

- progress evidence
- completion evidence
- a blocker fact pattern

When the execution agent hits a blocker, it reports the blocker to the supervisor and stops there.
It does not decide whether the blocker becomes a human gate, a redesign pass, or a retry.

## Round Behavior

An execution round starts from the approved contract slice and ends when one of these happens:

- the slice is implemented and evidenced
- the slice is blocked
- the supervisor sends a retry or continuation brief
- the supervisor ends the session

Within a round, the agent may run several local checks, but it should keep one clear supervisor-facing result for that round.

## Failure Handling

If the work cannot proceed, the agent records the reason and returns a blocker report.

If a check fails, the agent reports the failure and the evidence that shows the failure.

If the repo state is inconsistent, the agent reports the inconsistency rather than repairing beyond the approved slice.

If the execution agent cannot finish safely, it leaves enough evidence for the supervisor to route the next step.

## Do Not Do

- do not self-escalate blockers
- do not open or route human gates
- do not edit outside the approved slice
- do not use verification success as a substitute for acceptance
- do not hide failed checks
- do not convert implementation work into design work
- do not keep an old path alive just to avoid resolving the mainline contract
