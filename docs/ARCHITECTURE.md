# Architecture

## Goal

Build a small open-source project that lets Codex operate inside a persistent optimization loop instead of a one-shot conversation.

The design assumption is strict:

> Codex already has the action surface. This project turns that action surface into a durable run system with stable state, evaluation, control, and budget.

## System Shape

The system has four layers.

### 1. Intake Layer

Turns a user's natural-language goal into a structured run definition.

Inputs:

- goal
- artifact type
- optional artifact path
- optional budget overrides

Outputs:

- `goal.md`
- `spec.yaml`
- initial `state.json`

### 2. Evaluation Layer

Defines what "better" means.

V1 should always materialize:

- 3-5 hard checks
- 1 fixed judge prompt
- 1 user rubric that captures weighted preference, tradeoffs, and non-negotiables
- 1 calibration file with labeled examples of good and bad outputs
- 1 holdout gate
- a place to store side-information and per-case results
- a review phase where Codex and the user can refine the eval package before baseline starts

When the run is not ready yet, `draft-evals` should also materialize draft suggestion files so Codex has concrete starter content to refine instead of a blank workspace.

When the run is structurally ready, the harness should pause in an explicit confirmation phase and write review materials that Codex can use to walk the user through:

- whether the checks match the real success criteria
- whether the rubric captures the right weights, tradeoffs, and non-negotiables
- whether the calibration examples actually reflect the user's bar
- whether any check is vague or gameable
- whether train cases are representative
- whether holdout cases test transfer
- whether evaluation should use the host system's built-in subagent capability or an external panel
- if subagent-based, which host system should own that evaluator role
- what changes would make the user comfortable saying `start baseline`

This layer is the system bottleneck. If it is weak, the entire loop will optimize for nonsense.

For subjective or taste-sensitive tasks, the rubric and calibration examples are the main mechanism for turning "the user likes this more" into a repeatable evaluation signal without collapsing everything into brittle binary checks.

### 3. Execution Layer

Runs the artifact in a reproducible way.

V1 needs only a few adapters:

- `prompt`
- `document-copy`
- `repo-task`

Each adapter should produce:

- raw outputs
- structured metadata
- execution errors when present
- a phase-specific runner brief that tells Codex exactly how to execute the current experiment
- per-case briefs and score templates so execution can happen case by case without rebuilding the contract manually
- structured `execution_steps` and `agent_prompt` fields so Codex can start from the machine-readable payload itself

### 4. Control Layer

Owns the loop:

- eval drafting
- eval review and explicit confirmation
- baseline
- failure analysis
- one mutation
- reevaluation
- keep / discard
- logging
- budget accounting

## Codex Connection Model

The project connects to Codex through two mechanisms:

### 1. Shared Run State

Each run directory is a persistent workspace that Codex can read and update directly:

- `goal.md`
- `runbook.md`
- `spec.yaml`
- `state.json`
- `evals/`
- `cases/`
- `logs/`

This gives Codex a durable memory boundary outside the chat context.

### 2. Deterministic CLI Surface

The harness exposes file-system and CLI primitives that Codex can call with shell tools.

The intended split is:

- Codex handles semantic work: eval drafting, failure analysis, mutation design, report writing
- the harness handles deterministic work: initializing runs, reading state, validating structure, and later baseline/step/report execution

This split keeps the reasoning flexible while keeping the protocol stable.

## Repository Layout

```text
.
├── README.md
├── AGENT_DIGIVOLVE_HARNESS.md
├── IDEA_ESSENCE.md
├── docs/
├── src/
│   └── agent_digivolve_harness/
└── tests/
```

## Package Layout

```text
src/agent_digivolve_harness/
  __init__.py
  cli.py
  models.py
  scaffold.py
  yaml_utils.py
```

### `models.py`

Defines the persistent run contract:

- run spec
- budget
- acceptance
- runner config
- state

### `yaml_utils.py`

Small internal serializer for writing human-readable `spec.yaml` and `checks.yaml` without pulling in a dependency yet.

### `scaffold.py`

Creates the run directory and starter files.

### `runners.py`

Builds the execution brief for an active experiment.

It materializes `runner.json` and `runner.md` inside each experiment workspace so Codex can read:

- which adapter is active
- which artifact path is under evaluation
- which checks and judge files to use
- which summary file must be filled before finalize
- where the manifest lives
- how case outputs and scores should be written

It also materializes per-case briefs in the active experiment workspace so each case can be executed independently while still staying tied to the same manifest and scoring contract.

It also writes per-case JSON bundles so the same contract is available to CLI consumers without reparsing markdown.

### `cli.py`

Exposes the first command surface.

Implemented now:

- `init`
- `next`
- `work-packet`
- `advance`
- `run-loop`
- `draft-evals`
- `configure-evaluators`
- `confirm-evals`
- `baseline`
- `finalize-baseline`
- `step`
- `finalize-step`
- `runner`
- `cases`
- `case`
- `case-evals`
- `record-eval`
- `finalize-case`
- `openrouter-panel-eval`
- `record-case`
- `record-summary`
- `validate-case`
- `experiment-status`
- `complete-experiment`
- `report`
- `journal`
- `resume`
- `pause`

## Run Directory Contract

Each run is a standalone workspace:

```text
runs/<run-id>/
  goal.md
  runbook.md
  spec.yaml
  state.json
  artifact/
  evals/
  evaluations/
  cases/
  candidates/
  outputs/
  scores/
  logs/
  reports/
```

This is the state boundary that lets a run pause, resume, and survive context loss.

Each active experiment also gets its own runner brief:

```text
outputs/exp-000/
  manifest.json
  runner.json
  runner.md
```

and for iterative steps:

```text
outputs/exp-001/
  manifest.json
  runner.json
  runner.md
```

## Runbook

Every run should include a `runbook.md`.

That file is the Codex handoff contract for the run. It tells Codex:

- which files to read first
- which rules govern mutation and acceptance
- what the current phase means
- where to write results

The runbook makes a resumed session legible even when Codex has no memory of the earlier conversation.

## Why `init` Comes First

The first milestone is not "optimize everything." It is "make runs real."

Without a stable run scaffold, nothing else has a durable home:

- no persistent evals
- no baseline record
- no holdout
- no experiment log
- no recovery after interruption

So the correct first implementation step is:

1. initialize a run
2. write the contract to disk
3. make later phases plug into that contract

## V1 Commands

Implemented now:

```text
digivolve init <run-dir> --goal ... --artifact-type ...
digivolve next <run-dir>
digivolve work-packet <run-dir>
digivolve advance <run-dir>
digivolve run-loop <run-dir>
digivolve draft-evals <run-dir>
digivolve configure-evaluators <run-dir>
digivolve baseline <run-dir>
digivolve finalize-baseline <run-dir>
digivolve step <run-dir>
digivolve finalize-step <run-dir>
digivolve runner <run-dir>
digivolve cases <run-dir>
digivolve case <run-dir> <case-id>
digivolve case-evals <run-dir> <case-id>
digivolve record-eval <run-dir> <case-id>
digivolve finalize-case <run-dir> <case-id>
digivolve openrouter-panel-eval <run-dir> <case-id>
digivolve record-case <run-dir> <case-id>
digivolve record-summary <run-dir>
digivolve validate-case <run-dir> <case-id>
digivolve experiment-status <run-dir>
digivolve complete-experiment <run-dir>
digivolve report <run-dir>
digivolve journal <run-dir> --limit 5
digivolve pause <run-dir>
digivolve resume <run-dir> --limit 3
```

Near-term commands:

- `digivolve resume`

## Decision Rules

The future loop should enforce these rules by default:

1. No optimization without a baseline.
2. No keep decision without train improvement.
3. No keep decision if holdout regresses.
4. No multi-change mutation in a single step.
5. No state stored only in conversation memory.

`next` is the first explicit state-machine bridge between the harness and Codex. It turns the run directory into a machine-readable step source instead of relying on prose alone.

`work-packet` sits alongside `next`. It turns the current phase into a structured task packet that tells Codex what actual work is required now, which files to touch, and which commands will close the current gap.

`advance` sits one level above that bridge. It lets the harness perform the next legal lifecycle transition directly, so Codex can rely on the harness for phase dispatch instead of reimplementing that logic in conversation.

`run-loop` sits one level above `advance`. It keeps executing deterministic transitions until the run reaches a phase where semantic work is required, such as drafting evals, filling experiment case files, or writing a candidate mutation.

Case-level output and score files now act as the concrete execution contract inside each experiment workspace, and journal entries provide the append-only research log for later review or resumed Codex sessions.

Runner briefs now act as the execution contract for in-progress phases. When a run is in `baseline_in_progress` or `step_in_progress`, Codex should read the active `runner.md` before it starts writing outputs or scores.

The `runner`, `cases`, `case`, `case-evals`, `record-eval`, `finalize-case`, `openrouter-panel-eval`, `record-case`, `record-summary`, `validate-case`, `experiment-status`, and `complete-experiment` commands expose that same execution contract as machine-readable JSON, so Codex can inspect the active experiment, pull one case at a time, record independent evaluator verdicts, aggregate the official score, write the experiment summary, and finalize completion without reimplementing experiment parsing.

`pause` records the current operational phase, and `resume` builds a higher-level recovery payload that points Codex at the right report, recent journal entries, current workspace, and next action.

## `subagent` Policy

`subagent` should be treated as a search accelerator, not as the source of truth.

Allowed roles:

- failure clustering
- mutation brainstorming
- per-case diagnostics
- report drafting

Disallowed roles:

- final acceptance decision
- budget control
- live artifact promotion

## Roadmap

### Milestone 1

- package scaffold
- run initialization
- schema stabilization

### Milestone 2

- evaluation drafting templates
- Codex runbook and resume flow
- runner adapters
- baseline execution

### Milestone 3

- acceptance + holdout gate
- experiment logging and summary report

### Milestone 4

- optional dashboard
- richer diagnostics
- controlled parallel search via subagents
