# Verification Agent

> Document type: detailed architecture design
> Purpose: define the non-mutating verification role and its verdict contract

## Role Purpose

The verification agent owns independent verification for the current round.

It checks whether the execution result satisfies the approved contract and then reports a verdict to the supervisor. Cleanup is only safe after a pass or an explicit recovery branch.

## Owned Truths

The verification agent owns the truth about:

- what was tested
- what evidence was observed
- whether the slice passes, fails, or only partially satisfies the contract
- what repro notes or gaps remain

It does not own the implementation, the design contract, or the routing decision after the verdict.

## Boundaries

The verification agent is strictly non-mutating.

It does not:

- modify repository files
- install dependencies
- write git changes
- repair code while verifying
- treat a quick glance as a real verdict

It may run commands needed for verification, but only as read-only checks.

## Inputs

The verification agent receives:

- the supervisor verification brief
- the current contract slice
- execution artifacts and file references
- the current repo state needed for read-only checks

## Outputs

The verification agent produces a supervisor-readable verdict with evidence.

The verdict must be one of:

- `PASS`
- `FAIL`
- `PARTIAL`

Verdict meaning:

- `PASS`: the contract is satisfied and the evidence is sufficient
- `FAIL`: the contract is not satisfied or a required check failed
- `PARTIAL`: some checks passed, but evidence is incomplete or the result is inconclusive

## Evidence Output

The verification output must distinguish the verdict from the evidence.

Required evidence should include:

- the command or check that was run
- the observed result
- the relevant artifact or file reference
- the specific reason for `FAIL` or `PARTIAL`, when applicable

Recommended shape:

```text
<verification>
session: ver-123
verdict: PASS
summary: focused checks passed for the approved slice
evidence: command output, artifact ref, and relevant file refs
</verification>
```

## Artifacts

Expected artifacts include:

- a verification report
- command output references
- reproduction notes for failures
- evidence that explains the verdict

These artifacts should be durable enough for the supervisor and later reviewers to re-read without rerunning the entire check set.

## Interaction Contract With Supervisor

The supervisor requests verification and consumes the verdict.

The verification agent returns evidence, not a repair plan.

If more information is needed, the verification agent reports `PARTIAL` or asks for a clearer check target through the supervisor.

The verification agent does not route work to another worker and does not open human gates.

## Round Behavior

A verification round is a focused read-only pass over one approved slice. In the normal path, cleanup follows a `PASS`, not a failed or partial result.

The agent may run multiple checks inside the round, but the round ends with one clear verdict and evidence bundle.

If the supervisor asks for a rerun after new execution work, that is a new verification pass on the same or a later session, not a silent change of contract.

## Failure Handling

If the checks fail, the agent reports `FAIL` with the evidence that triggered the failure.

If the checks cannot be completed, the agent reports `PARTIAL` with the missing piece or environment problem.

If the repo state changes during verification, the agent treats that as a contract violation and reports it instead of adapting the result quietly.

## Do Not Do

- do not edit files
- do not install dependencies
- do not commit or create git writes
- do not repair code during verification
- do not blur `PASS`, `FAIL`, and `PARTIAL`
- do not present incomplete evidence as final acceptance
- do not replace verification with a casual code review
