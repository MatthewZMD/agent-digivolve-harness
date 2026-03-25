# Codex Integration

## Core Idea

`Agent Digivolve Harness` connects to Codex through a shared run directory and a small CLI surface.

The run directory is the persistent workspace.
The CLI is the deterministic control surface.
Codex is the reasoning and execution engine operating inside that workspace.

## The Connection Contract

Every run is a contract between the harness and Codex.

The harness creates:

- `goal.md`
- `runbook.md`
- `spec.yaml`
- `state.json`
- `evals/`
- `cases/`
- `logs/`
- `runner.md` / `runner.json` inside active experiment workspaces

Codex then reads and updates those files as it moves the run forward.

The harness also exposes:

- `digivolve next <run>`
- `digivolve work-packet <run>`
- `digivolve advance <run>`
- `digivolve run-loop <run>`
- `digivolve draft-evals <run>`
- `digivolve configure-evaluators <run>`
- `digivolve confirm-evals <run>`
- `digivolve baseline <run>`
- `digivolve finalize-baseline <run>`
- `digivolve step <run>`
- `digivolve finalize-step <run>`
- `digivolve runner <run>`
- `digivolve cases <run>`
- `digivolve case <run> <case-id>`
- `digivolve case-evals <run> <case-id>`
- `digivolve record-eval <run> <case-id>`
- `digivolve finalize-case <run> <case-id>`
- `digivolve openrouter-panel-eval <run> <case-id>`
- `digivolve record-case <run> <case-id>` for legacy non-independent evaluation runs only
- `digivolve record-summary <run>`
- `digivolve validate-case <run> <case-id>`
- `digivolve experiment-status <run>`
- `digivolve complete-experiment <run>`
- `digivolve report <run>`
- `digivolve journal <run>`
- `digivolve resume <run>`
- `digivolve pause <run>`

`next` gives Codex the current phase, required reads, allowed writes, and the next recommended action.

`work-packet` goes one step further and compiles the current run state into a structured task packet. For example, it can turn a `draft` run into an eval-setup to-do list, an `awaiting_confirmation` run into a collaborative eval review packet, or a `step_in_progress` run into a list of pending case tasks plus the exact commands needed to record and finalize them. Each work packet now also carries `execution_steps` and a `agent_prompt`, so Codex can pick up the current phase as a direct work order. During experiment phases, each pending case in the work packet also carries its own `execution_steps`, `agent_prompt`, and `evaluator_prompt`, so the packet can be handed straight to a subagent or external evaluator without another lookup step.

`advance` goes one step further: it performs the next legal lifecycle transition for the current phase and returns the resulting payload. This lets Codex stay thin and treat the harness as the phase controller.

`run-loop` goes one level higher: it keeps advancing deterministic transitions until the run reaches a phase that requires semantic work from Codex.

## What Codex Reads

When Codex is asked to continue a run, it should read these files in order:

1. `runbook.md`
2. `goal.md`
3. `spec.yaml`
4. `state.json`
5. `evals/checks.yaml`
6. `evals/judge.md`
7. `cases/train.jsonl`
8. `cases/holdout.jsonl`
9. `logs/experiments.tsv`
10. `logs/decisions.md`

When a run is in `awaiting_confirmation`, Codex should also read:

- `reports/eval_draft.md`
- `reports/eval_review.md`
- `reports/eval_review_prompt.md`

When a run is in `baseline_in_progress` or `step_in_progress`, Codex should also read the active experiment runner brief:

- `outputs/exp-000/runner.md` for baseline
- `outputs/exp-XXX/runner.md` for iterative steps

This gives Codex the full task boundary, current state, eval rules, and experiment history.

## What Codex Does

Codex is responsible for the semantic work inside the run:

- clarify missing eval details
- draft and refine checks
- review the eval package with the user and strengthen it from multiple angles
- run the baseline
- analyze failures
- propose one mutation
- execute the candidate
- summarize the result
- write logs and reports

Codex uses its existing abilities to do this:

- read and write files
- execute shell commands
- inspect command output
- edit artifacts
- use subagents for bounded parallel diagnostics

## What The Harness Does

The harness provides the stable outer loop:

- materialize the run directory
- keep state on disk
- define the schema and expected file locations
- expose CLI commands that Codex can call
- enforce structure for evaluation, logging, and budgeting

This keeps the system resumable and inspectable.

## First-Run Flow

1. The user runs:

```bash
digivolve init demo --goal "..." --artifact-type prompt
```

2. The harness creates the run directory under the system temporary directory and returns its canonical absolute path.

3. The user tells Codex:

```text
Continue the run in the absolute path returned by init, and follow its runbook.md.
```

4. Codex reads the run contract and advances the run from its current state.

If Codex needs an exact machine-readable instruction packet, it can run:

```bash
digivolve next demo
```

If Codex wants the current task expressed as a concrete work packet, it can run:

```bash
digivolve work-packet demo
```

If Codex wants the harness to perform the next legal lifecycle transition directly, it can run:

```bash
digivolve advance demo
```

If Codex wants the harness to keep moving until it hits a real work boundary, it can run:

```bash
digivolve run-loop demo
```

If Codex needs to validate or advance the drafting phase, it can run:

```bash
digivolve draft-evals demo
```

If the run is still incomplete, that command also writes draft suggestion files for checks, judge prompts, and train/holdout cases so Codex can refine concrete starting material.

If the run is waiting on explicit approval before baseline, Codex should use the review packet:

```bash
digivolve work-packet demo
digivolve configure-evaluators demo --mode subagent --subagent-system codex
digivolve confirm-evals demo --notes "User approved the eval package."
```

In that phase, Codex should summarize the current checks, cases, and evaluator strategy, explain the current proposal for independent evaluation, update that proposal with `configure-evaluators` if the user changes it, and wait until the user explicitly says baseline can start. The default proposal is the current host system's built-in subagent capability. Codex is only one host example; the same contract can map to systems like Claude Code or OpenCode.

If Codex needs to advance the run lifecycle further, it can use:

```bash
digivolve baseline demo
digivolve finalize-baseline demo
digivolve step demo
digivolve finalize-step demo
digivolve report demo
digivolve journal demo --limit 5
digivolve pause demo
digivolve resume demo --limit 3
```

If Codex wants the active execution contract as machine-readable JSON, it can use:

```bash
digivolve runner demo
digivolve cases demo
digivolve case demo train-1 --split train
digivolve case-evals demo train-1 --split train
digivolve openrouter-panel-eval demo train-1 --split train
digivolve experiment-status demo
digivolve record-eval demo train-1 --split train --check-id format --evaluator-id judge-1 --evaluator-kind subagent --passed --notes "Format passes."
digivolve record-eval demo train-1 --split train --check-id constraints --evaluator-id judge-1 --evaluator-kind subagent --passed --notes "Constraints pass."
digivolve record-eval demo train-1 --split train --check-id clarity --evaluator-id judge-1 --evaluator-kind subagent --failed --notes "Clarity fails."
digivolve finalize-case demo train-1 --split train
digivolve record-summary demo --summary "..."
digivolve validate-case demo train-1 --split train
digivolve complete-experiment demo
```

During execution phases, the active runner brief is the immediate contract for Codex. It points at:

- the adapter in use
- the artifact or candidate being executed
- the checks file and judge prompt to score against
- the summary file that must be filled before finalize
- the manifest path
- the output-writing rules for every case
- the independent evaluation contract for every case
- a structured `execution_steps` list
- a ready-to-use `agent_prompt`
- a ready-to-use `evaluator_prompt` overview plus per-check `evaluator_prompts`

The harness also writes one brief per case in the active experiment workspace, so Codex can pick up a single case file, execute it, write the raw output, hand each per-check evaluator prompt to an independent evaluator, and only then finalize the official score. Case bundles expose the same `execution_steps`, `agent_prompt`, `evaluator_prompt`, and `evaluator_prompts` fields for case-level execution. When the agreed external panel is OpenRouter-backed, Codex can use `openrouter-panel-eval` to run isolated `model x check` verdicts and record them directly.

The `runner`, `cases`, `case`, `case-evals`, `record-eval`, `finalize-case`, `openrouter-panel-eval`, `record-summary`, `validate-case`, `experiment-status`, and `complete-experiment` commands expose the same structure through JSON so Codex can operate directly on the active experiment without having to scrape markdown files or hand-edit score JSON.

## Resume Flow

The same handoff works later.

Because the state is on disk, a resumed Codex session can continue from the latest run status instead of reconstructing context from memory.

## Why This Matters

This connection model gives Codex something it normally lacks in a chat session:

- durable state
- repeatable evaluation
- explicit mutation boundaries
- a visible experiment ledger

That is what turns Codex from a one-shot responder into a run-based optimizer.
