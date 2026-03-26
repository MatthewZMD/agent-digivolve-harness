from __future__ import annotations

import json
from pathlib import Path

from .coordination import default_active_step
from .evaluation import design_calibration_examples, design_rubric_template, looks_design_oriented
from .git_ops import bootstrap_target
from .models import (
    AcceptanceSpec,
    BudgetSpec,
    EvaluationSpec,
    GitState,
    InitOptions,
    MutationScope,
    RunSpec,
    RunState,
    RunnerSpec,
    TargetRef,
)
from .yaml_utils import dump_yaml
from .workspace import resolve_run_dir


DIRECTORIES = [
    "evals",
    "evaluations",
    "cases",
    "worktrees",
    "outputs",
    "scores",
    "logs",
    "reports",
]


def create_run_scaffold(run_dir: Path, options: InitOptions) -> RunSpec:
    run_dir = resolve_run_dir(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory already exists and is not empty: {run_dir}")

    run_dir.mkdir(parents=True, exist_ok=True)
    for directory in DIRECTORIES:
        (run_dir / directory).mkdir(parents=True, exist_ok=True)

    run_id = options.run_id or run_dir.name
    target = bootstrap_target(
        run_id=run_id,
        artifact_type=options.artifact_type,
        artifact_path=options.artifact_path,
        placeholder_text=_artifact_placeholder(options.artifact_type),
    )

    spec = RunSpec(
        run_id=run_id,
        artifact_type=options.artifact_type,
        goal=options.goal,
        target=TargetRef(
            kind=target.kind,
            object_path=target.object_path,
            repo_root=target.repo_root,
            repo_relpath=target.repo_relpath,
        ),
        mutation_scope=_default_mutation_scope(options.artifact_type),
        constraints={"frozen_rules": _default_frozen_rules(options.artifact_type)},
        runner=_default_runner_spec(options.artifact_type),
        evaluation=EvaluationSpec(
            evaluator_mode=options.evaluator_mode,
            subagent_system=options.subagent_system,
            panel_size=options.panel_size,
            external_agents=_default_external_agents(options.evaluator_mode, options.panel_size),
        ),
        budget=BudgetSpec(
            max_experiments=options.max_experiments,
            max_judge_calls=options.max_judge_calls,
            max_subagents=options.max_subagents,
            max_wall_clock_minutes=options.max_wall_clock_minutes,
        ),
        acceptance=AcceptanceSpec(),
    )
    state = RunState(
        git=GitState(
            baseline_commit=target.initial_commit,
            current_commit=target.initial_commit,
            best_commit=target.initial_commit,
        )
    )

    _write_goal(run_dir / "goal.md", options.goal, spec, run_dir)
    _write_text(run_dir / "runbook.md", _runbook_template(spec.run_id, run_dir))
    _write_json(run_dir / "spec.json", spec.to_dict())
    _write_text(run_dir / "spec.yaml", dump_yaml(spec.to_dict()) + "\n")
    _write_json(run_dir / "state.json", state.to_dict())
    _write_text(run_dir / "evals" / "checks.yaml", _checks_template(options.artifact_type))
    _write_text(run_dir / "evals" / "judge.md", _judge_template(options.artifact_type))
    _write_text(run_dir / "evals" / "rubric.yaml", _rubric_template(options.artifact_type, options.goal))
    _write_text(
        run_dir / "evals" / "calibration.jsonl",
        _calibration_template(options.artifact_type, options.goal),
    )
    _write_text(run_dir / "cases" / "README.md", CASES_README)
    _write_text(run_dir / "cases" / "train.jsonl", "")
    _write_text(run_dir / "cases" / "holdout.jsonl", "")
    _write_text(
        run_dir / "logs" / "experiments.tsv",
        "experiment\tscore\tmax_score\tpass_rate\ttrain_status\tholdout_status\tdecision\tdescription\n",
    )
    _write_text(run_dir / "logs" / "journal.jsonl", "")
    _write_text(run_dir / "logs" / "events.jsonl", "")
    _write_text(run_dir / "logs" / "decisions.md", DECISIONS_TEMPLATE)
    _write_json(run_dir / "active_step.json", default_active_step())
    _write_text(run_dir / "reports" / "README.md", REPORTS_README)

    return spec


def _default_mutation_scope(artifact_type: str) -> MutationScope:
    if artifact_type == "document-copy":
        return MutationScope(
            mode="section", allowed_sections=["headline", "hero_body", "cta"]
        )
    if artifact_type == "prompt":
        return MutationScope(
            mode="section", allowed_sections=["system_rules", "examples", "output_format"]
        )
    return MutationScope(mode="repo", allowed_sections=[])


def _default_runner_spec(artifact_type: str) -> RunnerSpec:
    if artifact_type == "prompt":
        return RunnerSpec(
            type="prompt_runner",
            instruction_template="builtin://prompt-runner",
        )
    if artifact_type == "document-copy":
        return RunnerSpec(
            type="document_copy_runner",
            instruction_template="builtin://document-copy-runner",
        )
    return RunnerSpec(
        type="repo_task_runner",
        instruction_template="builtin://repo-task-runner",
    )


def _default_frozen_rules(artifact_type: str) -> list[str]:
    if artifact_type == "document-copy":
        return [
            "Do not invent product facts.",
            "Do not invent testimonials, logos, or customer names.",
        ]
    if artifact_type == "prompt":
        return [
            "Do not remove core safety constraints.",
            "Do not weaken explicit refusal or escalation rules without evidence.",
        ]
    return [
        "Do not change unrelated files.",
        "Do not remove safety or verification steps without evidence.",
    ]


def _artifact_placeholder(artifact_type: str) -> str:
    if artifact_type == "document-copy":
        return (
            "# Draft\n\n"
            "<replace this file with the copy you want to optimize>\n"
        )
    if artifact_type == "prompt":
        return (
            "# Prompt\n\n"
            "<replace this file with the prompt you want to optimize>\n"
        )
    return ""


def _write_goal(path: Path, goal: str, spec: RunSpec, run_dir: Path) -> None:
    contents = (
        f"# Goal\n\n"
        f"{goal}\n\n"
        f"## Metadata\n\n"
        f"- run_id: `{spec.run_id}`\n"
        f"- artifact_type: `{spec.artifact_type}`\n"
        f"- run_dir: `{run_dir.resolve()}`\n"
        f"- target_path: `{spec.target.object_path}`\n"
        f"- target_repo_root: `{spec.target.repo_root}`\n"
    )
    _write_text(path, contents)


def _runbook_template(run_id: str, run_dir: Path) -> str:
    return (
        "# Runbook\n\n"
        f"This directory is the source of truth for run `{run_id}`.\n\n"
        f"- run_dir: `{run_dir.resolve()}`\n\n"
        "## Read First\n\n"
        "1. `goal.md`\n"
        "2. `spec.yaml`\n"
        "3. `state.json`\n"
        "4. the target object path and repo root from `spec.yaml`\n"
        "5. `evals/checks.yaml`\n"
        "6. `evals/judge.md`\n"
        "7. `evals/rubric.yaml`\n"
        "8. `evals/calibration.jsonl`\n"
        "9. `cases/train.jsonl`\n"
        "10. `cases/holdout.jsonl`\n"
        "11. `logs/experiments.tsv`\n"
        "12. `logs/decisions.md`\n\n"
        "## Operating Rules\n\n"
        "- Keep all state on disk.\n"
        "- Respect `mutation_scope` and `frozen_rules` in `spec.yaml`.\n"
        "- Use `evals/rubric.yaml` to encode weighted user preferences, tradeoffs, and non-negotiables.\n"
        "- Use `evals/calibration.jsonl` to preserve user-labeled examples of good and bad outputs.\n"
        "- Review and confirm the eval package before baseline when confirmation is required.\n"
        "- Every evaluation must come from an independent evaluator. Do not self-grade the executed case.\n"
        "- Run a baseline before any optimization.\n"
        "- Make one mutation per experiment.\n"
        "- Only keep a change when train improves and holdout does not regress.\n"
        "- Log every experiment.\n\n"
        "## Phase Guide\n\n"
        "- `draft`: refine evals and cases until the run is ready for baseline.\n"
        "- `awaiting_confirmation`: review the drafted eval package with the user and wait for explicit approval to start baseline.\n"
        "- `ready`: run the baseline as experiment `#0`.\n"
        "- `baseline_in_progress`: read `outputs/exp-000/runner.md`, fill raw outputs, record independent evaluator verdicts, aggregate official scores, then finalize baseline.\n"
        "- `baseline_complete` or `iterating`: analyze failures, create one candidate, evaluate with an independent evaluator, decide, and log.\n"
        "- `step_in_progress`: read `outputs/exp-XXX/runner.md`, mutate the git worktree once, commit it, gather independent evaluator verdicts, aggregate official scores, and finalize the step decision.\n"
        "- `paused`: resume from the latest state and logs.\n"
        "- `complete`: write the final summary in `reports/`.\n"
    )


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def _checks_template(artifact_type: str) -> str:
    examples = {
        "document-copy": [
            {
                "id": "specificity",
                "question": "Does the output contain at least one concrete number, fact, or verifiable claim?",
                "pass": "A reader can point to a specific, checkable detail.",
                "fail": "The copy stays generic and makes only vague promises.",
            },
            {
                "id": "cta",
                "question": "Does the output end with a specific next step?",
                "pass": "The CTA tells the reader exactly what to do next.",
                "fail": "The ending is generic or has no actionable next step.",
            },
        ],
        "prompt": [
            {
                "id": "format",
                "question": "Does the response follow the requested output format exactly?",
                "pass": "Required sections and ordering are present.",
                "fail": "The output format is missing or inconsistent.",
            },
            {
                "id": "constraint_integrity",
                "question": "Does the output respect the core prompt constraints?",
                "pass": "No forbidden behavior appears in the response.",
                "fail": "The response violates a stated prompt rule.",
            },
        ],
        "repo-task": [
            {
                "id": "task_complete",
                "question": "Did the run complete the requested task?",
                "pass": "The target behavior is implemented or fixed.",
                "fail": "The task remains unresolved or partially resolved.",
            },
            {
                "id": "verification",
                "question": "Is there concrete verification for the result?",
                "pass": "Tests, commands, or outputs validate the change.",
                "fail": "The change has no trustworthy verification.",
            },
        ],
    }
    payload = {"checks": examples[artifact_type]}
    return dump_yaml(payload) + "\n"


def _judge_template(artifact_type: str) -> str:
    return (
        "# Judge Prompt\n\n"
        "You are the fixed overall evaluator for this run.\n\n"
        f"Artifact type: `{artifact_type}`\n\n"
        "Your job is not to rewrite the artifact. Your job is to judge whether the new candidate is genuinely better than the baseline or previous best.\n\n"
        "You must behave like an independent evaluator, not like the executor that produced the output.\n\n"
        "Focus on:\n\n"
        "- overall task success\n"
        "- signs of checklist gaming\n"
        "- regressions in clarity, usefulness, or correctness\n"
        "- whether the candidate would still look good on unseen cases\n"
    )


def _rubric_template(artifact_type: str, goal: str) -> str:
    if artifact_type == "repo-task" and looks_design_oriented(goal):
        return dump_yaml(design_rubric_template()) + "\n"

    templates = {
        "document-copy": {
            "criteria": [
                {
                    "id": "specificity_and_truthfulness",
                    "weight": 3,
                    "priority": "must",
                    "guidance": "Prefer concrete, truthful copy with verifiable detail over generic marketing language.",
                },
                {
                    "id": "clarity_and_flow",
                    "weight": 2,
                    "priority": "should",
                    "guidance": "Prefer clean narrative flow and readable structure over clever but confusing phrasing.",
                },
                {
                    "id": "cta_strength",
                    "weight": 2,
                    "priority": "should",
                    "guidance": "Prefer a specific next step that feels credible and relevant.",
                },
            ],
            "non_negotiables": [
                "Do not invent product facts, testimonials, or customer names.",
                "Prefer credible specificity over hype.",
            ],
            "tradeoffs": [
                "If specificity conflicts with flourish, prefer specificity.",
                "If brevity conflicts with clarity, prefer clarity.",
            ],
        },
        "prompt": {
            "criteria": [
                {
                    "id": "task_success",
                    "weight": 3,
                    "priority": "must",
                    "guidance": "Prefer outputs that solve the user's actual request directly.",
                },
                {
                    "id": "constraint_integrity",
                    "weight": 3,
                    "priority": "must",
                    "guidance": "Prefer outputs that preserve explicit rules and required format exactly.",
                },
                {
                    "id": "clarity_and_directness",
                    "weight": 2,
                    "priority": "should",
                    "guidance": "Prefer concise, readable outputs over bloated or ornamental phrasing.",
                },
            ],
            "non_negotiables": [
                "Do not weaken explicit safety or refusal rules.",
                "Do not trade correctness for style.",
            ],
            "tradeoffs": [
                "If brevity conflicts with correctness, prefer correctness.",
                "If polish conflicts with strict format compliance, prefer format compliance.",
            ],
        },
        "repo-task": {
            "criteria": [
                {
                    "id": "task_completion",
                    "weight": 3,
                    "priority": "must",
                    "guidance": "Prefer end-to-end task completion over partial progress that only looks plausible.",
                },
                {
                    "id": "verification_depth",
                    "weight": 3,
                    "priority": "must",
                    "guidance": "Prefer concrete tests or runtime evidence over unsupported claims of success.",
                },
                {
                    "id": "scope_and_maintainability",
                    "weight": 2,
                    "priority": "should",
                    "guidance": "Prefer minimal, relevant changes that preserve code quality and repository boundaries.",
                },
            ],
            "non_negotiables": [
                "Do not claim a fix without evidence.",
                "Do not change unrelated files or behavior.",
            ],
            "tradeoffs": [
                "If speed conflicts with verification, prefer verification.",
                "If adding code conflicts with keeping scope tight, prefer the narrower change.",
            ],
        },
    }
    return dump_yaml(templates[artifact_type]) + "\n"


def _calibration_template(artifact_type: str, goal: str) -> str:
    if artifact_type == "repo-task" and looks_design_oriented(goal):
        return "\n".join(json.dumps(row) for row in design_calibration_examples()) + "\n"

    templates = {
        "document-copy": [
            {
                "id": "good-1",
                "label": "good",
                "input": "Write a short product intro.",
                "output": "Track every clinic handoff with a shared timeline, audit trail, and one-click follow-up reminders.",
                "why": "Concrete and credible. It names specific capabilities without inventing outcomes.",
            },
            {
                "id": "bad-1",
                "label": "bad",
                "input": "Write a short product intro.",
                "output": "Transform your workflow with a revolutionary platform built for the future.",
                "why": "Generic hype. It says almost nothing verifiable.",
            },
        ],
        "prompt": [
            {
                "id": "good-1",
                "label": "good",
                "input": "Answer in 3 bullets.",
                "output": "- Point one\n- Point two\n- Point three",
                "why": "Follows the required format exactly and stays direct.",
            },
            {
                "id": "bad-1",
                "label": "bad",
                "input": "Answer in 3 bullets.",
                "output": "Here is a detailed explanation with several paragraphs and no bullets.",
                "why": "Misses the requested format and adds filler.",
            },
        ],
        "repo-task": [
            {
                "id": "good-1",
                "label": "good",
                "input": "Fix the failing endpoint.",
                "output": "Changed the route handler, added a regression test, and verified it with `pytest tests/api/test_routes.py`.",
                "why": "Completes the task and includes verification evidence.",
            },
            {
                "id": "bad-1",
                "label": "bad",
                "input": "Fix the failing endpoint.",
                "output": "I believe this should now work after some cleanup.",
                "why": "No evidence, vague scope, and no concrete confirmation.",
            },
        ],
    }
    return "\n".join(json.dumps(row) for row in templates[artifact_type]) + "\n"


def _default_external_agents(evaluator_mode: str, panel_size: int) -> list[str]:
    if evaluator_mode != "external_panel":
        return []
    count = max(1, int(panel_size))
    return [f"external-agent-{index}" for index in range(1, count + 1)]


CASES_README = """# Cases

Use `train.jsonl` for search cases and `holdout.jsonl` for non-regression checks.

Suggested JSONL shape:

```json
{"id":"case-1","input":"...","notes":"optional"}
```
"""


REPORTS_README = """# Reports

Store baseline summaries, step summaries, and final run reports here.
"""


DECISIONS_TEMPLATE = """# Decisions

## Baseline

Not run yet.

## Mutation Log

No experiments recorded yet.
"""
