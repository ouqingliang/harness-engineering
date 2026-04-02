# AGENT.md

This file is the stable entry point for `harness-engineering/`.

## Mission

Build and maintain the harness that lets AI workers continue meaningful software delivery with:

- bounded context
- explicit role separation
- durable runtime state
- human escalation only at real decision gates

## Stable Truths

- the harness is a control shell around agents
- `supervisor` owns orchestration truth
- specialist agents own narrow scopes
- routine blockers should be handled inside the harness first
- only `communication-agent` should face the human
- active runtime state lives under `.harness/`
- work is not done until the required verification has actually passed

## Default Reading Order

1. `README.md`
2. `memory/index.md`
3. the relevant file under `memory/doc/`
4. the specific `agents/<role>/agent.json`
5. the specific `agents/<role>/system.md`

## Working Rules

- DO NOT FORGET: use subagent to finish complicated jobs
- keep the runtime simple
- keep all runtime roles under `agents/`
- keep role boundaries sharp
- read repository docs and runtime text artifacts as UTF-8
- write repository docs and runtime text artifacts as UTF-8
- pass work through handoffs and reports, not implicit chat memory
- let `supervisor` answer ordinary blockers first
- keep active runtime state under `.harness/`
- require real verification before closing work

## Decision Gates

Escalate to the human only for:

- architecture contract changes
- destructive actions
- security boundary changes
- external side effects with cost or risk
- unresolved priority conflicts

Everything else should stay inside the autonomous loop.

When you escalate, make sure to:

- clearly explain the situation, do not just dump the problem
- explain the situation in Chinese
- explain the situation in detail, human do not know what you are fucking doing
- provide options for the human to choose from, do not just ask a question