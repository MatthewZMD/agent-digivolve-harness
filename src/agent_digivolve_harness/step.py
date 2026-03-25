from __future__ import annotations

import json
from pathlib import Path

from .casefiles import collect_case_results, initialize_case_artifacts
from .git_ops import (
    cherry_pick_commit,
    create_experiment_worktree,
    ensure_target_commit_matches,
    has_uncommitted_changes,
    remove_worktree,
    target_path_in_checkout,
    validate_candidate_commit,
)
from .journal import append_journal_entry
from .runners import write_case_briefs, write_runner_brief
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def prepare_step(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)

    if state.get("baseline_score") is None:
        raise ValueError("Baseline must be completed before starting a step.")
    if state.get("status") == "step_in_progress":
        raise ValueError("A step is already in progress for this run.")

    git_state = state.setdefault("git", {})
    target = spec["target"]
    repo_root = Path(target["repo_root"]).resolve()
    parent_commit = git_state.get("current_commit") or git_state.get("best_commit")
    if not parent_commit:
        raise ValueError("Run state is missing the current target commit.")
    ensure_target_commit_matches(repo_root, parent_commit)
    if has_uncommitted_changes(repo_root):
        raise ValueError(
            f"Target repository must be clean before creating a new experiment: {repo_root}"
        )

    experiment_id = int(state.get("current_experiment", 0)) + 1
    exp_name = f"exp-{experiment_id:03d}"
    worktree_path = run_dir / "worktrees" / exp_name
    output_dir = run_dir / "outputs" / exp_name
    score_dir = run_dir / "scores" / exp_name
    report_path = run_dir / "reports" / f"{exp_name}.md"

    create_experiment_worktree(repo_root, worktree_path, parent_commit)
    target_path = target_path_in_checkout(target, worktree_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    score_dir.mkdir(parents=True, exist_ok=True)

    manifest = _build_manifest(run_dir, experiment_id)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    initialize_case_artifacts(run_dir, manifest)

    runner_json_path, runner_md_path = write_runner_brief(
        run_dir,
        spec,
        manifest,
        kind="step",
        output_dir=output_dir,
        target_path=target_path,
        worktree_path=worktree_path,
        parent_commit=parent_commit,
    )
    case_brief_paths = write_case_briefs(
        run_dir,
        spec,
        manifest,
        kind="step",
        output_dir=output_dir,
        target_path=target_path,
        worktree_path=worktree_path,
        parent_commit=parent_commit,
    )
    _write_text(score_dir / "summary.json", _step_summary_template())
    _write_text(report_path, _step_report(spec, experiment_id, worktree_path, target_path, parent_commit))

    state["status"] = "step_in_progress"
    state["pending_experiment"] = experiment_id
    git_state["active_worktree"] = str(worktree_path.resolve())
    git_state["active_target_path"] = str(target_path.resolve())
    git_state["active_parent_commit"] = parent_commit
    git_state["active_candidate_commit"] = None
    save_run_state(run_dir, state)

    return {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "state_status": state["status"],
        "experiment_id": experiment_id,
        "next_action": "finalize_step",
        "manifest_path": str(manifest_path),
        "summary_path": str((score_dir / "summary.json").resolve()),
        "worktree_path": str(worktree_path.resolve()),
        "target_path": str(target_path.resolve()),
        "written_files": [
            str(manifest_path),
            str(runner_json_path.resolve()),
            str(runner_md_path.resolve()),
            *[str(path.resolve()) for path in case_brief_paths],
            str((score_dir / "summary.json").resolve()),
            str(report_path.resolve()),
            str((run_dir / "state.json").resolve()),
        ],
    }


def finalize_step(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)

    experiment_id = state.get("pending_experiment")
    if experiment_id is None:
        raise ValueError("No step is currently in progress.")

    exp_name = f"exp-{int(experiment_id):03d}"
    summary_path = run_dir / "scores" / exp_name / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing step summary file: {summary_path}")

    git_state = state.setdefault("git", {})
    repo_root = Path(spec["target"]["repo_root"]).resolve()
    worktree_path = Path(git_state.get("active_worktree") or "")
    parent_commit = git_state.get("active_parent_commit")
    if not worktree_path or not worktree_path.exists():
        raise FileNotFoundError("Active experiment worktree is missing.")
    if not parent_commit:
        raise ValueError("Run state is missing the active parent commit.")

    candidate_commit = validate_candidate_commit(worktree_path, parent_commit)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    case_results = collect_case_results(run_dir, int(experiment_id))
    validated = _validate_step_summary(summary, case_results)
    best_metrics = _validate_existing_metrics(state.get("current_best_metrics"))

    train_rate = _rate(validated["train_score"], validated["train_max_score"])
    holdout_rate = _rate(validated["holdout_score"], validated["holdout_max_score"])
    best_train_rate = _rate(best_metrics["train_score"], best_metrics["train_max_score"])
    best_holdout_rate = _rate(best_metrics["holdout_score"], best_metrics["holdout_max_score"])
    overall_pass_rate = round(
        ((validated["train_score"] + validated["holdout_score"]) /
         (validated["train_max_score"] + validated["holdout_max_score"])) * 100,
        2,
    )

    decision = "keep" if train_rate > best_train_rate and holdout_rate >= best_holdout_rate else "discard"
    promoted_commit = None
    if decision == "keep":
        ensure_target_commit_matches(repo_root, git_state.get("current_commit"))
        promoted_commit = cherry_pick_commit(repo_root, candidate_commit)
        state["status"] = "iterating"
        state["current_experiment"] = int(experiment_id)
        state["best_candidate"] = exp_name
        state["best_score"] = overall_pass_rate
        state["current_best_metrics"] = validated
        git_state["current_commit"] = promoted_commit
        git_state["best_commit"] = promoted_commit
    else:
        state["status"] = "baseline_complete" if int(experiment_id) == 1 and state.get("best_candidate") == "baseline" else "iterating"
        state["current_experiment"] = int(experiment_id)

    git_state["active_candidate_commit"] = candidate_commit
    remove_worktree(repo_root, worktree_path)
    git_state["active_worktree"] = None
    git_state["active_target_path"] = None
    git_state["active_parent_commit"] = None
    git_state["active_candidate_commit"] = None

    state["pending_experiment"] = None
    budget_used = state.setdefault("budget_used", {})
    budget_used["experiments"] = int(budget_used.get("experiments", 0)) + 1
    save_run_state(run_dir, state)

    _append_step_row(run_dir / "logs" / "experiments.tsv", int(experiment_id), overall_pass_rate, validated, decision)
    _append_step_decision(
        run_dir / "logs" / "decisions.md",
        int(experiment_id),
        decision,
        overall_pass_rate,
        validated,
        candidate_commit=candidate_commit,
        promoted_commit=promoted_commit,
    )
    append_journal_entry(
        run_dir / "logs" / "journal.jsonl",
        experiment_id=int(experiment_id),
        kind="step",
        decision=decision,
        pass_rate=overall_pass_rate,
        summary=validated["summary"],
        details={
            "mutation_description": validated["mutation_description"],
            "candidate_commit": candidate_commit,
            "promoted_commit": promoted_commit,
            "train_score": validated["train_score"],
            "train_max_score": validated["train_max_score"],
            "holdout_score": validated["holdout_score"],
            "holdout_max_score": validated["holdout_max_score"],
            "cases": case_results["cases"],
        },
    )

    return {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "state_status": state["status"],
        "experiment_id": int(experiment_id),
        "decision": decision,
        "candidate_commit": candidate_commit,
        "promoted_commit": promoted_commit,
        "best_score": state.get("best_score"),
        "next_action": "step",
        "written_files": [
            str((run_dir / "state.json").resolve()),
            str((run_dir / "logs" / "experiments.tsv").resolve()),
            str((run_dir / "logs" / "decisions.md").resolve()),
        ],
    }


def _build_manifest(run_dir: Path, experiment_id: int) -> dict:
    cases = []
    for split in ["train", "holdout"]:
        path = run_dir / "cases" / f"{split}.jsonl"
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            case = dict(payload)
            case.update(
                {
                    "split": split,
                    "id": payload["id"],
                    "input": payload["input"],
                    "output_file": f"outputs/exp-{experiment_id:03d}/{split}-{payload['id']}.md",
                    "score_file": f"scores/exp-{experiment_id:03d}/{split}-{payload['id']}.json",
                    "brief_file": f"outputs/exp-{experiment_id:03d}/cases/{split}-{payload['id']}.md",
                    "bundle_file": f"outputs/exp-{experiment_id:03d}/cases/{split}-{payload['id']}.json",
                }
            )
            cases.append(case)
    return {
        "experiment_id": experiment_id,
        "kind": "step",
        "summary_file": f"scores/exp-{experiment_id:03d}/summary.json",
        "cases": cases,
    }


def _step_summary_template() -> str:
    payload = {
        "mutation_description": "Replace this with one sentence describing the committed mutation.",
        "train_score": 0,
        "train_max_score": 0,
        "holdout_score": 0,
        "holdout_max_score": 0,
        "summary": "Replace this with the result summary for the candidate.",
    }
    return json.dumps(payload, indent=2) + "\n"


def _step_report(
    spec: dict,
    experiment_id: int,
    worktree_path: Path,
    target_path: Path,
    parent_commit: str,
) -> str:
    return (
        f"# Experiment {experiment_id}\n\n"
        f"- run_id: `{spec['run_id']}`\n"
        f"- artifact_type: `{spec['artifact_type']}`\n"
        f"- worktree_path: `{worktree_path.resolve()}`\n"
        f"- target_path: `{target_path.resolve()}`\n"
        f"- parent_commit: `{parent_commit}`\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Required Output\n\n"
        "- one committed mutation in the experiment worktree\n"
        "- raw outputs and per-case scores\n"
        "- a filled step summary JSON\n"
    )


def _validate_step_summary(summary: dict, case_results: dict) -> dict:
    required_fields = [
        "mutation_description",
        "summary",
    ]
    missing = [field for field in required_fields if field not in summary]
    if missing:
        raise ValueError(f"Step summary is missing fields: {', '.join(missing)}")

    if not isinstance(summary["mutation_description"], str) or not summary["mutation_description"].strip():
        raise ValueError("mutation_description must be a non-empty string")
    if not isinstance(summary["summary"], str) or not summary["summary"].strip():
        raise ValueError("summary must be a non-empty string")

    _validate_optional_numeric_match(summary, case_results)

    return {
        "mutation_description": summary["mutation_description"].strip(),
        "train_score": float(case_results["train_score"]),
        "train_max_score": float(case_results["train_max_score"]),
        "holdout_score": float(case_results["holdout_score"]),
        "holdout_max_score": float(case_results["holdout_max_score"]),
        "summary": summary["summary"].strip(),
    }


def _validate_existing_metrics(metrics: dict | None) -> dict:
    if not metrics:
        raise ValueError("Run state is missing current_best_metrics")
    return {
        "train_score": float(metrics["train_score"]),
        "train_max_score": float(metrics["train_max_score"]),
        "holdout_score": float(metrics["holdout_score"]),
        "holdout_max_score": float(metrics["holdout_max_score"]),
    }


def _rate(score: float, max_score: float) -> float:
    return score / max_score


def _append_step_row(path: Path, experiment_id: int, pass_rate: float, summary: dict, decision: str) -> None:
    row = (
        f"{experiment_id}\t{summary['train_score'] + summary['holdout_score']}\t"
        f"{summary['train_max_score'] + summary['holdout_max_score']}\t"
        f"{pass_rate}%\tmeasured\tmeasured\t{decision}\t{summary['mutation_description']}\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(row)


def _append_step_decision(
    path: Path,
    experiment_id: int,
    decision: str,
    pass_rate: float,
    summary: dict,
    *,
    candidate_commit: str,
    promoted_commit: str | None,
) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n"
            f"## Experiment {experiment_id} — {decision}\n\n"
            f"- pass_rate: `{pass_rate}%`\n"
            f"- mutation: {summary['mutation_description']}\n"
            f"- candidate_commit: `{candidate_commit}`\n"
            + (f"- promoted_commit: `{promoted_commit}`\n" if promoted_commit else "")
            + f"- result: {summary['summary']}\n"
        )


def _write_text(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


def _validate_optional_numeric_match(summary: dict, case_results: dict) -> None:
    fields = [
        "train_score",
        "train_max_score",
        "holdout_score",
        "holdout_max_score",
    ]
    for field in fields:
        if field not in summary:
            continue
        value = summary[field]
        if not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be numeric")
        expected = float(case_results[field])
        if value != 0 and float(value) != expected:
            raise ValueError(f"{field} does not match aggregated case scores")
