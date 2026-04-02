# Escalation Rules

Escalate to the human only when the active work hits:

- architecture contract change
- destructive action
- security boundary change
- external side effect with cost or irreversible impact
- unresolved conflict between project goals

`decision-agent` is the semantic boundary for blocker triage and human judgment.
The human communication surface is runtime-owned and is not a worker role.
`verification-agent` is the acceptance boundary for read-only checks and evidence.
The supervisor still owns the final routing outcome and must record it explicitly.
