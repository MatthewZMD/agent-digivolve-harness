from __future__ import annotations

import json
from pathlib import Path

from .coordination import active_step_path, ensure_coordination_files, events_path
from .readiness import assess_run_readiness
from .workspace import resolve_run_dir, wait_for_run_initialization

REQUIRED_FILES = [
    "runbook.md",
    "goal.md",
    "spec.json",
    "spec.yaml",
    "state.json",
    "evals/checks.yaml",
    "evals/judge.md",
    "evals/rubric.yaml",
    "evals/calibration.jsonl",
    "cases/train.jsonl",
    "cases/holdout.jsonl",
    "logs/experiments.tsv",
    "logs/decisions.md",
]


def build_next_payload(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    wait_for_run_initialization(run_dir, required_files=REQUIRED_FILES)
    _validate_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    state = _load_json(run_dir / "state.json")
    spec = _load_json(run_dir / "spec.json")
    readiness = assess_run_readiness(run_dir)
    phase = resolve_phase(run_dir, state, readiness)

    common_reads = [
        "runbook.md",
        "goal.md",
        "spec.yaml",
        "state.json",
        "evals/checks.yaml",
        "evals/judge.md",
        "evals/rubric.yaml",
        "evals/calibration.jsonl",
        "logs/experiments.tsv",
        "logs/decisions.md",
        str(Path(active_step_path(run_dir)).relative_to(run_dir)),
        str(Path(events_path(run_dir)).relative_to(run_dir)),
    ] + _artifact_reads(spec)

    phase_table = {
        "draft": {
            "next_action": "draft_evals",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
            ],
            "allowed_writes": [
                "evals/checks.yaml",
                "evals/judge.md",
                "evals/rubric.yaml",
                "evals/calibration.jsonl",
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "state.json",
                "logs/decisions.md",
            ],
            "success_condition": (
                "Checks, judge rules, rubric, calibration examples, train cases, and holdout cases are ready for baseline."
            ),
            "notes": [
                "Refine evaluation before spending budget on baseline.",
                "Keep the checks binary, the rubric preference-aware, and the holdout set distinct from train cases.",
            ],
        },
        "awaiting_confirmation": {
            "next_action": "confirm_evals",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "reports/eval_draft.md",
                "reports/eval_review.md",
                "reports/eval_review_prompt.md",
                "reports/eval_explained.md",
            ],
            "allowed_writes": [
                "evals/checks.yaml",
                "evals/judge.md",
                "evals/rubric.yaml",
                "evals/calibration.jsonl",
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "reports/eval_review.md",
                "reports/eval_review_prompt.md",
                "reports/eval_confirmation.md",
                "state.json",
            ],
            "success_condition": (
                "The user explicitly confirms the eval package, including evaluator strategy, and `confirm_evals` is recorded."
            ),
            "notes": [
                "Review the current checks, judge, rubric, calibration examples, cases, and evaluator strategy with the user before baseline.",
                "Do not start baseline until the user explicitly approves the eval package.",
            ],
        },
        "ready": {
            "next_action": "run_baseline",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
            ],
            "allowed_writes": [
                "outputs/",
                "scores/",
                "logs/experiments.tsv",
                "logs/journal.jsonl",
                "logs/decisions.md",
                "reports/",
                "state.json",
            ],
            "success_condition": (
                "Experiment #0 is recorded with baseline outputs, scores, and summary."
            ),
            "notes": [
                "Do not mutate the artifact during baseline.",
                "Use the current committed target exactly as-is for experiment #0.",
            ],
        },
        "baseline_in_progress": {
            "next_action": "complete_baseline",
            "required_reads": common_reads
            + [
                "outputs/exp-000/manifest.json",
                "outputs/exp-000/runner.json",
                "outputs/exp-000/runner.md",
                "outputs/exp-000/cases",
                "scores/exp-000/summary.json",
                "reports/baseline.md",
            ],
            "allowed_writes": [
                "outputs/exp-000/",
                "scores/exp-000/",
                "reports/baseline.md",
                "logs/decisions.md",
                "state.json",
            ],
            "success_condition": (
                "Baseline outputs, case-level scores, and summary.json are complete and can be finalized."
            ),
            "notes": [
                "Use the manifest as the source of truth for baseline case execution.",
                "Do not mutate the artifact during baseline.",
            ],
        },
        "baseline_complete": {
            "next_action": "step",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "logs/journal.jsonl",
            ],
            "allowed_writes": [
                "candidates/",
                "outputs/",
                "scores/",
                "logs/experiments.tsv",
                "logs/journal.jsonl",
                "logs/decisions.md",
                "reports/",
                "state.json",
            ],
            "success_condition": (
                "One candidate is evaluated, a keep/discard decision is logged, and the state advances."
            ),
            "notes": [
                "Make one mutation only.",
                "Only keep a candidate when train improves and holdout does not regress.",
            ],
        },
        "step_in_progress": {
            "next_action": "finalize_step",
            "required_reads": _step_in_progress_reads(run_dir, state, common_reads),
            "allowed_writes": _step_in_progress_writes(run_dir, state),
            "success_condition": (
                "The candidate summary is complete and a keep/discard decision can be finalized."
            ),
            "notes": [
                "Mutate one thing only.",
                "Candidate output and score files should match the current step manifest.",
            ],
        },
        "iterating": {
            "next_action": "step",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "logs/journal.jsonl",
            ],
            "allowed_writes": [
                "candidates/",
                "outputs/",
                "scores/",
                "logs/experiments.tsv",
                "logs/journal.jsonl",
                "logs/decisions.md",
                "reports/",
                "state.json",
            ],
            "success_condition": (
                "The next experiment produces a logged keep/discard outcome and updated run state."
            ),
            "notes": [
                "Use the latest best candidate as the starting point.",
                "Respect `mutation_scope` and frozen rules in `spec.yaml`.",
            ],
        },
        "paused": {
            "next_action": "resume",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "logs/journal.jsonl",
            ],
            "allowed_writes": [
                "state.json",
                "logs/decisions.md",
                "reports/",
            ],
            "success_condition": (
                "The run state is reactivated and the next operational phase is clear."
            ),
            "notes": [
                "Inspect the latest logs before resuming work.",
                "Determine whether the run should return to ready, baseline_complete, or iterating.",
            ],
        },
        "replan_required": {
            "next_action": "replan",
            "required_reads": common_reads
            + [
                "cases/train.jsonl",
                "cases/holdout.jsonl",
                "logs/journal.jsonl",
            ],
            "allowed_writes": [
                "goal.md",
                "spec.json",
                "spec.yaml",
                "evals/",
                "cases/",
                "reports/",
                "logs/decisions.md",
                "logs/events.jsonl",
                "active_step.json",
                "state.json",
            ],
            "success_condition": (
                "The direction change is reconciled, stale work has been replaced, and a replan is recorded."
            ),
            "notes": [
                "Do not resume execution until the new direction is reflected in the run artifacts.",
                "Use the event log and active step snapshot to understand what was interrupted or invalidated.",
            ],
        },
        "complete": {
            "next_action": "report",
            "required_reads": common_reads
            + [
                "logs/journal.jsonl",
            ],
            "allowed_writes": [
                "reports/",
            ],
            "success_condition": "A final summary report is written.",
            "notes": [
                "Do not mutate the artifact after completion unless the user explicitly starts a new run.",
            ],
        },
    }

    phase_payload = phase_table[phase]
    return {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "state_status": state.get("status", "draft"),
        "phase": phase,
        "readiness": readiness,
        "next_action": phase_payload["next_action"],
        "required_reads": _to_abs_paths(run_dir, phase_payload["required_reads"]),
        "allowed_writes": _to_abs_paths(run_dir, phase_payload["allowed_writes"]),
        "success_condition": phase_payload["success_condition"],
        "notes": phase_payload["notes"],
    }


def resolve_phase(run_dir: Path, state: dict, readiness: dict[str, object]) -> str:
    explicit = state.get("status", "draft")
    if explicit in {
        "paused",
        "complete",
        "baseline_in_progress",
        "step_in_progress",
        "awaiting_confirmation",
        "replan_required",
    }:
        return explicit
    if state.get("replan_required"):
        return "replan_required"

    spec = _load_json(run_dir / "spec.json")
    return infer_operational_phase(run_dir, spec, state, readiness)


def infer_operational_phase(run_dir: Path, spec: dict, state: dict, readiness: dict[str, object]) -> str:
    if state.get("pending_experiment") is not None:
        return "step_in_progress"
    if (run_dir / "outputs" / "exp-000" / "manifest.json").exists() and state.get("baseline_score") is None:
        return "baseline_in_progress"
    if state.get("current_experiment", 0) > 0:
        return "iterating"
    if state.get("baseline_score") is not None:
        return "baseline_complete"
    if readiness["ready_for_baseline"]:
        require_confirmation = spec.get("evaluation", {}).get("require_confirmation", True)
        if require_confirmation and not state.get("eval_confirmed", False):
            return "awaiting_confirmation" if state.get("eval_drafted", False) else "draft"
        return "ready"
    return "draft"


def _validate_run_dir(run_dir: Path) -> None:
    missing = [item for item in REQUIRED_FILES if not (run_dir / item).exists()]
    if missing:
        formatted = ", ".join(missing)
        raise FileNotFoundError(f"Run directory is missing required files: {formatted}")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_abs_paths(run_dir: Path, entries: list[str]) -> list[str]:
    resolved: list[str] = []
    for entry in entries:
        candidate = Path(entry).expanduser()
        if candidate.is_absolute():
            resolved.append(str(candidate.resolve()))
        else:
            resolved.append(str((run_dir / candidate).resolve()))
    return resolved


def _step_in_progress_reads(run_dir: Path, state: dict, common_reads: list[str]) -> list[str]:
    exp_id = int(state.get("pending_experiment") or (state.get("current_experiment", 0) + 1))
    exp_name = f"exp-{exp_id:03d}"
    git_state = state.get("git", {})
    reads = common_reads + [
        f"outputs/{exp_name}/manifest.json",
        f"outputs/{exp_name}/runner.json",
        f"outputs/{exp_name}/runner.md",
        f"outputs/{exp_name}/cases",
        f"scores/{exp_name}/summary.json",
        f"reports/{exp_name}.md",
    ]
    active_worktree = git_state.get("active_worktree")
    active_target_path = git_state.get("active_target_path")
    if active_worktree:
        reads.append(active_worktree)
    if active_target_path:
        reads.append(active_target_path)
    return reads


def _step_in_progress_writes(run_dir: Path, state: dict) -> list[str]:
    exp_id = int(state.get("pending_experiment") or (state.get("current_experiment", 0) + 1))
    exp_name = f"exp-{exp_id:03d}"
    entries = [
        f"outputs/{exp_name}",
        f"scores/{exp_name}",
        f"reports/{exp_name}.md",
        "logs/experiments.tsv",
        "logs/journal.jsonl",
        "logs/decisions.md",
        "state.json",
    ]
    active_worktree = state.get("git", {}).get("active_worktree")
    if active_worktree:
        entries.append(active_worktree)
    return _to_abs_paths(run_dir, entries)


def _artifact_reads(spec: dict) -> list[str]:
    target = spec.get("target", {})
    target_path = target.get("object_path")
    repo_root = target.get("repo_root")
    reads: list[str] = []
    if target_path:
        reads.append(target_path)
    if repo_root and repo_root != target_path:
        reads.append(repo_root)
    return reads
