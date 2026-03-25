---
name: agent-digivolve-harness-operator
description: Turn a vague user request like “make this artifact better or more reliable” into a durable optimization run with fixed evaluation, baseline, one bounded mutation per round, keep-or-revert acceptance, holdout protection, and external memory on disk. Use when an agent should operate or continue a `digivolve` run, or when a prompt, skill, document, or repo task should be improved through an eval-driven iterative loop instead of one-shot edits.
---

# Agent Digivolve Harness Entrypoint

## Overview

Use the harness to turn repeated local trial-and-error into a stable optimization loop.
Treat run state on disk as authoritative. Session memory, compaction, and partial chat history are not authoritative.
Start from `run-loop` and `work-packet`, then follow the current packet instead of inventing an ad hoc workflow.
This skill is agent-system agnostic. It should tell the current agent how to operate the harness whether the host is Codex, Claude Code, OpenCode, or another system with comparable file, shell, and optional subagent capabilities.

## Authority Model

- The harness decides the current phase, next action, and legal writes.
- The current `work-packet`, `resume` payload, `runner`, and `case` bundles carry the dynamic instructions for this run.
- Do not try to memorize all phase details from this skill. Reread the current harness payload when state changes.
- When the harness provides `execution_steps`, `agent_prompt`, or `recommended_commands`, treat them as the immediate contract unless they clearly conflict with explicit user instructions.
- The skill provides static operating principles. The harness provides the current run truth.

## Bootstrap

- Do not assume this skill lives inside the `agent-digivolve-harness` repository. In the intended packaged setup, the skill can be installed separately from the Python package.
- Resolve a usable CLI first. Prefer this order:
  1. `AGENT_DIGIVOLVE_HARNESS_CLI` if the host already provides an explicit CLI path
  2. `digivolve` if it is already on `PATH`
  3. `./scripts/ensure_cli.sh` from this skill directory, which can install the package into a skill-local virtual environment
- `scripts/ensure_cli.sh` supports two bootstrap sources:
  1. `AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT` for local development or an unpacked source tree
  2. `AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC` for a wheel, VCS URL, or PyPI package spec
- If neither environment variable is set, the bootstrap script assumes the published package name is `agent-digivolve-harness`.
- Repo-local `PYTHONPATH=src python3 -m agent_digivolve_harness.cli ...` is only a development fallback when the source tree is explicitly available. It is not the primary packaged-skill path.
- Resolve the active run directory before doing semantic work. The CLI materializes runs under the system temporary directory, so a short run ref like `demo` is only a handle; the harness payloads should expose the canonical absolute run path.
- Start with:
  1. `digivolve run-loop <run>`
  2. `digivolve work-packet <run>`
- If the run is paused or was interrupted earlier, use `digivolve resume <run>` and read the recovery payload before continuing.
- Treat the current work packet as the primary task boundary. Read `runbook.md` and any required files the packet names when the run is still being initialized or when the packet points there.
- When the harness exposes a user-facing status or progress summary, use it to anchor those explanations.

## Execution Rules

- Prefer harness commands over manual edits to manifest or score JSON files.
- Never treat chat continuity as a substitute for rereading run artifacts.
- Before each meaningful phase action, explain the current state to the user in plain language: what is happening, what you are about to do next, and why it matters. Translate harness terms instead of assuming the user already knows them.
- During baseline, use the current artifact as-is.
- During a step, make exactly one bounded mutation before running cases, and respect `mutation_scope` and `frozen_rules`.
- Treat evaluator selection as part of the eval package review with the user. Do not assume the evaluator path is fixed unless the run artifacts already say so.
- If the evaluator path is not already fixed by the run artifacts, explicitly ask the user to choose it. Do not silently default to `subagent` or `external_panel`.
- If the user changes evaluator path or evaluator identities during review, record that choice with `digivolve configure-evaluators <run> ...` before confirming.
- Use `experiment-status`, `runner`, `cases`, and `case` when the active execution contract is unclear.
- When evaluation mode is `subagent`, use the current host system's built-in subagent capability. The important contract is not which brand of agent is running, but that the evaluator is independent from the executor while still operating inside the same host system. Codex, Claude Code, and OpenCode are examples, not special cases.
- When evaluation mode is `external_panel` and the chosen panel is OpenRouter-backed, prefer `digivolve openrouter-panel-eval <run> <case-id> ...` over hand-recording verdicts.
- Hand subagents a single case bundle when parallelizing. Do not invent a separate contract.
- Keep state on disk inside the run directory. If a command updates state, reread the current work packet before making the next decision.
- Never run `confirm-evals` without explicit user approval.

## Host Agent Capabilities

- Minimum host agent requirements:
  1. file access in the run directory
  2. shell access to run `digivolve`
  3. ability to read current work packets and write back through harness commands
- Optional host capability:
  1. built-in subagents for `evaluation_mode=subagent`
- If the current host does not support built-in subagents, prefer `external_panel` and configure explicit external evaluators with the user.

## Interventions

- If the user adds context or preference without invalidating the current step, record it with `digivolve note <run> --message '...'`.
- If the user asks to stop while you are still active, use `digivolve interrupt <run> --reason '...'`.
- If the session was killed out-of-band and you are only seeing the aftermath on resume, rely on the recovery payload instead of assuming you could have recorded the interrupt yourself.
- If the user changes goal, success criteria, evaluation standards, or overall direction, use `digivolve change-direction <run> --request '...'` instead of improvising a continuation.
- If the harness moves the run into `replan_required`, update the relevant run artifacts first, then record the reconciled direction with `digivolve replan <run> --summary '...'`.
- After any interruption, note, direction change, or replan, reread the current `work-packet` or `resume` payload before continuing.

## Command Pattern

- Preferred command form: `<resolved digivolve CLI> <subcommand> <run>`
- Skill-local bootstrap helper: `./scripts/ensure_cli.sh`
- Development fallback when source is explicitly available: `PYTHONPATH=src python3 -m agent_digivolve_harness.cli <subcommand> <run>`
- First-choice flow:
  1. `run-loop`
  2. `work-packet`
  3. phase-specific commands from the packet
- Recovery flow:
  1. `resume`
  2. read `active_step.json` and `logs/events.jsonl`
  3. follow the resumed phase or replan requirement
- Confirmation flow:
  1. `work-packet`
  2. discuss checks, cases, and evaluator strategy with the user
  3. if the evaluator path changes, run `configure-evaluators`
  4. only then run `confirm-evals`
- Standard write-back flow for execution:
  1. `case`
  2. `record-eval` once per check, or `openrouter-panel-eval` for isolated external `model x check` verdicts
  3. `finalize-case`
  4. `validate-case`
  5. `record-summary`
  6. `complete-experiment`
