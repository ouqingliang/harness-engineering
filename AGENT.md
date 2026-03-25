# AGENT.md

This file is the stable entry point for `harness-engineering/`.

## Mission

Build and maintain the harness that lets AI workers continue meaningful software delivery with:

- bounded context
- explicit role separation
- durable artifacts
- human escalation only at decision gates

## Stable Truths

- the harness is a control system, not a product feature folder
- the harness is independent from the old `EngineerNode` runtime model
- `supervisor` owns orchestration truth
- specialist agents own narrow scopes and must leave durable artifacts
- human involvement is reserved for decision gates, not routine execution
- active long-term task memory is center-owned in deployment

## Default Reading Order

1. `README.md`
2. `memory/index.md`
3. the relevant file under `memory/doc/`
4. the specific `agents/<role>/agent.json`
5. the specific `agents/<role>/system.md`

## Working Rules

- keep role boundaries sharp
- write artifacts that another agent can resume from
- prefer explicit contracts over improvised shared assumptions
- do not reintroduce `EngineerNode` as a live architecture object
- do not mix product-runtime concerns with harness-runtime concerns
- keep active runtime state under `.harness`, not `.agents`

## Decision Gates

Escalate to the human only for:

- architecture contract changes
- destructive actions
- security boundary changes
- external side effects with cost or risk
- unresolved priority conflicts

Everything else should stay in the autonomous loop.
