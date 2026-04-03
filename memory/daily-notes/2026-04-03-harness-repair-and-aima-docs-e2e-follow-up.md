# Daily Note: Harness Repair and AIMA Docs E2E Follow-Up

## Purpose

This note records the work completed on 2026-04-03 across `harness-engineering` and the sibling `AIMA-refactor` repository.

It is intentionally detailed because this session covered:

- the already-landed supervisor-centered role refactor
- additional runtime fixes in the execution lane
- external-project blocker cleanup through isolated worktrees and subagents
- repair of supervised worktree submodule hydration
- a fresh `docs/` end-to-end run that is still in progress and is not yet complete

This note does not claim the full `AIMA-refactor/docs` end-to-end flow is done.
At the time of writing, that full run is still active and a new execution-lane blocker has been observed.

## Verified Session Outcomes

### 1. The supervisor-centered role architecture is the active harness direction

The intended runtime shape remains:

- `supervisor` is the only scheduler
- `decision`, `design`, `execution`, `verification`, and `cleanup` are the active role set
- human interaction happens only through explicit supervisor-owned decision gates
- mutating worker roles run in supervisor-managed git worktrees

This session continued from the already-landed role-migration work on `main`, including:

- `cae1d26` `WS-02: make supervisor routing event-driven`
- `ca99868` `WS-03: migrate role specs and scheduler to decision/verification`
- `e4d44cc` `Retire legacy worker semantics`
- `3edf253` `test: expand role migration regression matrix`
- `19615c5` `Fix verification launcher and legacy audit routing`

The current local workspace still carries additional uncommitted follow-up fixes described below.

### 2. The human page and human-gate communication chain were re-verified after the refactor

The runtime-owned human communication surface is present and wired through:

- `main.py`
- `lib/communication_api.py`
- `runners/codex_app_server.py`

Verified behavior in this session:

- `main.py run` starts the local human page
- `GET /` renders the monitor page
- the page can submit replies through `POST /human/reply`
- replies persist into runtime inbox state
- the scheduler can pause on a gate and resume after a human reply
- the CLI long-running process remains alive through waiting and after completion

Fresh verification run in this session:

- `python -m unittest tests.test_runner tests.test_human_gate_flow tests.e2e.test_harness_engineering_long_run -v`

Observed result:

- `9` tests passed

This is the current evidence that the refactored web page and human communication lane are wired and test-covered inside the harness repository itself.

### 3. The execution-lane Codex contract was corrected and regression-tested

Additional runtime repairs were made in:

- `lib/scheduler_components/execution.py`
- `tests/test_runtime_files.py`

The corrected behavior covered the following issues:

- the execution prompt now frontloads the final JSON-only reply contract
- spawn sessions use stdin prompt transport through `codex exec -`
- resume sessions use the correct command ordering for workspace and session restoration
- execution option schema handling now expects the full item shape instead of accepting incomplete option objects

Fresh verification run in this session:

- `python -m unittest tests.test_runtime_files -v`

Observed result:

- `22` tests passed
- `1` legacy test remained skipped by design

This is the current evidence that the execution contract fixes are covered in the local harness test suite.

### 3.1. The execution lane now resolves harness artifact paths before launching nested `codex exec`

After the first fresh `docs/` run exposed a new blocker, the execution lane received one more targeted repair in:

- `lib/scheduler_components/execution.py`

The corrected behavior is:

- execution request payloads now persist `schema_path` and `output_path` as absolute harness paths
- saved execution requests also normalize older relative `schema_path` and `output_path` entries against `HARNESS_ROOT`
- nested `codex exec` launches therefore no longer depend on the current working directory of the external project worktree when reading harness-owned schema and result files

Fresh verification after this change:

- `python -m unittest tests.test_runtime_files -v`

Observed result:

- `22` tests passed
- `1` legacy test remained skipped by design

This change was made specifically to address the external-worktree failure where `codex exec` could not read a relative schema file path from inside the assigned project worktree.

### 4. Supervised external worktrees now hydrate submodules instead of assuming they already exist

An earlier blocker in the external-project flow was that fresh supervised worktrees created from an external repository did not initialize submodules.

That caused verification inside the external worktree to fail when the worktree tried to execute:

- `python harness-engineering/main.py --format json`

The worktree repair was implemented in:

- `lib/worktree.py`
- `tests/test_worktree.py`

The important behavior change is:

- the harness no longer blindly runs `git submodule update --init --recursive` against every gitlink it can discover
- it first reads `.gitmodules`
- it only hydrates submodules that have both configured `path` and `url`
- it then runs a targeted update for those configured submodule paths

This matters because the sibling `AIMA-refactor` repository currently contains a gitlink for `auto-meta-agent`, but `.gitmodules` only provides a complete `path`/`url` pair for `harness-engineering`.

Without this targeted behavior, the worktree setup fails with:

- `fatal: No url found for submodule path 'auto-meta-agent' in .gitmodules`

Fresh verification run in this session:

- `python -m unittest tests.test_worktree -v`

Observed result:

- `4` tests passed

This is the current evidence that supervised worktree hydration now handles the real repository shape more safely.

### 5. Temporary harness runtime directories in the local workspace were cleaned up

During this session, stale local `.tmp*` runtime directories under `harness-engineering/` were removed to make the next run easier to inspect and less likely to reuse confusing prior state.

That cleanup was intentionally limited to the current harness workspace.

### 6. A four-worktree follow-up refactor was completed in the harness main workspace

Later in the same session, four additional follow-up tasks were delegated to four separate git worktrees and then integrated manually into the main harness workspace.

Those four tasks were:

- remove the `AIMA-refactor` hardcoded project identity from the execution-agent prompt
- make baseline-doc preference configurable instead of only using the built-in AIMA baseline list
- add an explicit `--project-root` runtime override instead of relying only on walking upward from `--doc-root`
- fix Windows promotion failures caused by trying to promote runtime-owned `.harness/` paths back into the canonical project root

The follow-up integration changed these areas:

- `main.py`
- `config.yaml`
- `lib/scheduler.py`
- `lib/scheduler_components/execution.py`
- `lib/worktree.py`
- `tests/test_runtime_files.py`
- `tests/test_worktree.py`

The resulting behavior is now:

- `main.py run` advertises and accepts `--project-root`
- the mission records `project_root` from the explicit override when provided
- the mission also records `preferred_baseline_docs` from `config.yaml`
- the scheduler refresh path preserves that configured baseline-doc list instead of silently reverting to hardcoded defaults
- the execution-agent prompt derives its project identity from design-contract context or canonical project-root context rather than always saying `AIMA-refactor`
- worktree promotion ignores runtime-owned `.harness/` paths, so accepted execution worktrees no longer try to delete or overwrite launcher-run directories under the runtime root

Fresh verification after this follow-up integration:

- `python -m unittest tests.test_worktree -v`
- `python -m unittest tests.test_runtime_files -v`
- `python main.py run --help`
- `python -m py_compile main.py lib/scheduler.py lib/scheduler_components/execution.py lib/worktree.py`

Observed result:

- `tests.test_worktree`: `6` tests passed
- `tests.test_runtime_files`: `22` tests passed, `1` skipped by design
- `main.py run --help` showed the new `--project-root` flag
- the Python compile pass succeeded with no syntax errors

An additional focused in-workspace verification also confirmed:

- an explicit `project_root` override is written into the mission metadata
- configured `preferred_baseline_docs` propagate into the scheduler-refreshed `doc_bundle`

This follow-up work improves both project portability and Windows runtime robustness, but it still does not claim that the full `AIMA-refactor/docs` end-to-end run is complete.

## External Project Blocker Sweep Completed Earlier In This Session

### 1. External-project blocker fixes were implemented through isolated worktrees and subagents

The sibling `AIMA-refactor` repository previously had a group of blocker failures that prevented the `docs/` end-to-end flow from making clean progress.

Those blockers were addressed using isolated worktrees and delegated workers, then integrated and verified manually.

The resulting commits on `AIMA-refactor/main` are:

- `fe791a8` `Fix sync-ssh output on Windows`
- `7a0f920` `Fix engineer health row ordering`
- `a42100b` `Fix client artifact contract tests`
- `f243dc1` `Fix repo-root alembic test on Windows`

### 2. Those external-project fixes were re-verified directly in the canonical repository

The following commands were run successfully in the canonical `AIMA-refactor` checkout earlier in this session:

- `pytest tests/test_center_alembic_from_repo_root.py -q`
- `cd src/center && PYTHONPATH=. pytest tests -q`
- `cd src/client && go test ./... -v`
- `cd src/engineer/access && go test ./... -v`
- `python harness-engineering/main.py --format json`

Observed result at that time:

- the targeted blocker regressions passed in the canonical repository checkout

This was the basis for moving from blocker cleanup back into a fresh `docs/` end-to-end run.

## Current `docs/` End-to-End Status

### 1. The full `AIMA-refactor/docs` end-to-end run is not complete yet

The active runtime root at the time of writing is:

- `.tmp-aima-docs-e2e-final6`

Fresh runtime inspection in this session used:

- `python .\main.py status --memory-root .tmp-aima-docs-e2e-final6`

At the time of inspection, the runtime state showed:

- `mission.status = active`
- `state.status = running`
- `state.active_agent = execution`
- current route outcome = `reopen_execution`
- no human gate is pending

So the current run is not blocked on human input.
It is blocked inside the autonomous execution and verification loop.

Between the first and latest inspection in this same session, the supervisor also:

- auto-replanned repeated blocker failures instead of escalating to the human
- converted the Phase 2 problem into blocker slices
- relaunched `execution` against a blocker slice rather than stopping the run

This confirms that the current runtime is continuing autonomously, even though it is still not converging.

### 2. The run has already completed multiple execution-plus-verification cycles for Phase 2

Observed sequence in the current run:

- `design` prepared the slice for `Phase 2: Replace the center data mainline`
- `execution` launched in a supervised external worktree
- all five post-execution verification commands passed inside that external worktree
- `verification` reopened execution instead of accepting the slice
- repeated reopen results triggered supervisor auto-replan
- the supervisor created blocker slices for the same Phase 2 failure signature
- the active run then relaunched `execution` for the blocker slice instead of opening a human gate

The verification commands that passed inside the worktree were:

- `pytest tests/test_center_alembic_from_repo_root.py -q`
- `pytest tests -q` under `src/center`
- `go test ./... -v` under `src/client`
- `go test ./... -v` under `src/engineer/access`
- `python harness-engineering/main.py --format json`

The run therefore did not fail because these post-execution checks were red.
It is repeatedly reopening because the worker-launch contract still fails before the execution artifact can be accepted.

### 3. The immediate blocker is now inside the execution subagent launch contract

Fresh evidence from:

- `.tmp-aima-docs-e2e-final6/.harness/artifacts/cycle-2910ab2a527645d6ac9dba0d63694d50/06-execution-execution.json`
- `.tmp-aima-docs-e2e-final6/.harness/reports/cycle-2910ab2a527645d6ac9dba0d63694d50-08-verification.json`

shows that the current reopen was caused by:

- `Execution subagent exited with code 1.`

The captured stderr from the execution subagent shows the more specific reason:

- the Codex invocation could not read its output schema file
- the path was passed as `.tmp-aima-docs-e2e-final6\.harness\artifacts\...05-execution-codex-request-schema.json`
- the child process was launched from the external worktree
- that relative path therefore did not resolve inside the external worktree
- the command failed with Windows `os error 3` (`path not found`)

So the current blocker is not:

- a failed product verification command
- a missing human decision
- the earlier submodule-initialization problem

The current blocker is:

- execution still passes relative harness artifact paths into a process that is launched from an external project worktree

### 4. A secondary architecture-alignment signal is still visible in the external worktree

The verification run inside the external worktree also showed that:

- `python harness-engineering/main.py --format json`

returned the older role list:

- `communication`
- `design`
- `execution`
- `audit`
- `cleanup`

rather than the current local harness role set:

- `decision`
- `design`
- `execution`
- `verification`
- `cleanup`

This means the external repository's checked-out `harness-engineering` submodule commit is still behind the current local refactor state.

That observation did not directly trigger the current reopen.
The direct reopen cause was the execution subagent exit with the unresolved schema path.

It is still important, because the external-project end-to-end baseline is not yet aligned to the newest local harness runtime shape.

## What Is Done vs. What Is Still Open

### Done and verified inside `harness-engineering`

- human page and human gate runtime path re-verified
- execution contract regression fixes added and re-tested
- supervised worktree submodule hydration repaired and re-tested
- stale local runtime directories cleaned up

### Done and verified earlier in the canonical `AIMA-refactor` checkout

- Windows alembic regression fix
- client artifact contract regression fix
- engineer health ordering regression fix
- Windows sync-ssh regression fix

### Still open

- the full `AIMA-refactor/docs` end-to-end run is still not complete
- the current active run is reopening inside `execution` because the subagent launch still uses a relative schema/output artifact path from an external worktree context
- the external repository's `harness-engineering` submodule pointer is still behind the current local harness refactor state

## Recommended Next Actions

The next work should start from these facts:

- fix the execution subagent invocation so schema and output artifact paths are safe when the worker process is launched from an external supervised worktree
- align the external repository's `harness-engineering` submodule pointer with the current refactored harness state
- only after those two are aligned, judge the next `docs/` end-to-end result as the meaningful closure run
