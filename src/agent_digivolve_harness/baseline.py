from __future__ import annotations

import json
from pathlib import Path

from .casefiles import collect_case_results, initialize_case_artifacts
from .git_ops import current_commit, ensure_target_commit_matches, has_uncommitted_changes
from .journal import append_journal_entry
from .readiness import assess_run_readiness, readiness_recommendations
from .runners import write_case_briefs, write_runner_brief
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def prepare_baseline(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)
    require_confirmation = spec.get("evaluation", {}).get("require_confirmation", True)

    if not readiness["ready_for_baseline"]:
        return {
            "run_dir": str(run_dir),
            "run_id": spec["run_id"],
            "state_status": state["status"],
            "ready_for_baseline": False,
            "readiness": readiness,
            "recommendations": readiness_recommendations(readiness),
            "next_action": "draft_evals",
        }
    if require_confirmation and not state.get("eval_confirmed", False):
        return {
            "run_dir": str(run_dir),
            "run_id": spec["run_id"],
            "state_status": state["status"],
            "ready_for_baseline": True,
            "confirmation_required": True,
            "next_action": "confirm_evals",
            "reason": "The eval package must be explicitly confirmed before baseline starts.",
        }

    target = spec["target"]
    repo_root = Path(target["repo_root"]).resolve()
    if has_uncommitted_changes(repo_root):
        raise ValueError(f"Target repository must be clean before baseline starts: {repo_root}")
    head_commit = current_commit(repo_root)
    if head_commit is None:
        raise ValueError(f"Target repository does not have a valid HEAD commit: {repo_root}")
    git_state = state.setdefault("git", {})
    current_state_commit = git_state.get("current_commit")
    if state.get("baseline_score") is None and current_state_commit != head_commit:
        git_state["baseline_commit"] = head_commit
        git_state["current_commit"] = head_commit
        git_state["best_commit"] = head_commit
        save_run_state(run_dir, state)
    else:
        ensure_target_commit_matches(repo_root, current_state_commit)

    manifest = _build_manifest(run_dir)
    output_dir = run_dir / "outputs" / "exp-000"
    score_dir = run_dir / "scores" / "exp-000"
    output_dir.mkdir(parents=True, exist_ok=True)
    score_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    initialize_case_artifacts(run_dir, manifest)
    runner_json_path, runner_md_path = write_runner_brief(
        run_dir,
        spec,
        manifest,
        kind="baseline",
        output_dir=output_dir,
    )
    case_brief_paths = write_case_briefs(
        run_dir,
        spec,
        manifest,
        kind="baseline",
        output_dir=output_dir,
    )
    _write_text(output_dir / "README.md", _baseline_output_readme(manifest))
    _write_text(score_dir / "summary.json", _baseline_summary_template())
    report_path = run_dir / "reports" / "baseline.md"
    _write_text(report_path, _baseline_report(spec, manifest))

    state["status"] = "baseline_in_progress"
    save_run_state(run_dir, state)

    return {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "state_status": state["status"],
        "next_action": "complete_baseline",
        "manifest_path": str(manifest_path),
        "summary_path": str((score_dir / "summary.json").resolve()),
        "written_files": [
            str(manifest_path),
            str(runner_json_path.resolve()),
            str(runner_md_path.resolve()),
            *[str(path.resolve()) for path in case_brief_paths],
            str((output_dir / "README.md").resolve()),
            str((score_dir / "summary.json").resolve()),
            str(report_path.resolve()),
            str((run_dir / "state.json").resolve()),
        ],
    }


def finalize_baseline(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    summary_path = run_dir / "scores" / "exp-000" / "summary.json"

    if not summary_path.exists():
        raise FileNotFoundError(f"Missing baseline summary file: {summary_path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    case_results = collect_case_results(run_dir, experiment_id=0)
    validated = _validate_summary(summary, case_results)

    pass_rate = round(
        ((validated["train_score"] + validated["holdout_score"]) /
         (validated["train_max_score"] + validated["holdout_max_score"])) * 100,
        2,
    )

    state["status"] = "baseline_complete"
    state["current_experiment"] = 0
    state["pending_experiment"] = None
    state["baseline_score"] = pass_rate
    state["best_score"] = pass_rate
    state["current_best_metrics"] = validated
    save_run_state(run_dir, state)

    _append_experiment_row(run_dir / "logs" / "experiments.tsv", pass_rate, validated)
    _update_decisions(run_dir / "logs" / "decisions.md", pass_rate, validated)
    append_journal_entry(
        run_dir / "logs" / "journal.jsonl",
        experiment_id=0,
        kind="baseline",
        decision="baseline",
        pass_rate=pass_rate,
        summary=validated["summary"],
        details={
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
        "baseline_score": pass_rate,
        "next_action": "step",
        "written_files": [
            str((run_dir / "state.json").resolve()),
            str((run_dir / "logs" / "experiments.tsv").resolve()),
            str((run_dir / "logs" / "decisions.md").resolve()),
        ],
    }


def _build_manifest(run_dir: Path) -> dict:
    train_cases = _load_cases(run_dir / "cases" / "train.jsonl", split="train")
    holdout_cases = _load_cases(run_dir / "cases" / "holdout.jsonl", split="holdout")
    cases = train_cases + holdout_cases

    manifest_cases = []
    for case in cases:
        manifest_case = dict(case)
        manifest_case.update(
            {
                "output_file": f"outputs/exp-000/{case['split']}-{case['id']}.md",
                "score_file": f"scores/exp-000/{case['split']}-{case['id']}.json",
                "brief_file": f"outputs/exp-000/cases/{case['split']}-{case['id']}.md",
                "bundle_file": f"outputs/exp-000/cases/{case['split']}-{case['id']}.json",
            }
        )
        manifest_cases.append(manifest_case)

    return {
        "experiment_id": 0,
        "kind": "baseline",
        "summary_file": "scores/exp-000/summary.json",
        "cases": manifest_cases,
    }


def _load_cases(path: Path, split: str) -> list[dict]:
    rows: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        row = dict(payload)
        row["split"] = split
        row["id"] = payload["id"]
        row["input"] = payload["input"]
        rows.append(row)
    return rows


def _baseline_output_readme(manifest: dict) -> str:
    lines = [
        "# Baseline Output Workspace",
        "",
        "Use `manifest.json` as the source of truth for baseline case execution.",
        "",
        "For each case in the manifest:",
        "",
        "- produce one raw output file at the listed `output_file` path",
        "- produce one score file at the listed `score_file` path",
        "- fill `scores/exp-000/summary.json` after all case-level scoring is complete",
        "",
        "Only record the committed target as-is for experiment `#0`.",
        "",
        f"Total cases: {len(manifest['cases'])}",
    ]
    return "\n".join(lines) + "\n"


def _baseline_summary_template() -> str:
    payload = {
        "train_score": 0,
        "train_max_score": 0,
        "holdout_score": 0,
        "holdout_max_score": 0,
        "summary": "Replace this with a concise baseline summary.",
    }
    return json.dumps(payload, indent=2) + "\n"


def _baseline_report(spec: dict, manifest: dict) -> str:
    return (
        "# Baseline\n\n"
        f"- run_id: `{spec['run_id']}`\n"
        f"- artifact_type: `{spec['artifact_type']}`\n"
        f"- total_cases: `{len(manifest['cases'])}`\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Required Output\n\n"
        "- raw outputs for every train and holdout case\n"
        "- per-case score files\n"
        "- a filled `scores/exp-000/summary.json`\n"
    )


def _validate_summary(summary: dict, case_results: dict) -> dict:
    required_fields = ["summary"]
    missing = [field for field in required_fields if field not in summary]
    if missing:
        formatted = ", ".join(missing)
        raise ValueError(f"Baseline summary is missing fields: {formatted}")
    if not isinstance(summary["summary"], str) or not summary["summary"].strip():
        raise ValueError("summary must be a non-empty string")

    _validate_optional_numeric_match(summary, case_results)

    return {
        "train_score": float(case_results["train_score"]),
        "train_max_score": float(case_results["train_max_score"]),
        "holdout_score": float(case_results["holdout_score"]),
        "holdout_max_score": float(case_results["holdout_max_score"]),
        "summary": summary["summary"].strip(),
    }


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


def _append_experiment_row(path: Path, pass_rate: float, summary: dict) -> None:
    row = (
        f"0\t{summary['train_score'] + summary['holdout_score']}\t"
        f"{summary['train_max_score'] + summary['holdout_max_score']}\t"
        f"{pass_rate}%\tmeasured\tmeasured\tbaseline\toriginal artifact\n"
    )
    existing = path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    if len(lines) > 1:
        lines = lines[:1]
        existing = "\n".join(lines) + "\n"
    path.write_text(existing + row, encoding="utf-8")


def _update_decisions(path: Path, pass_rate: float, summary: dict) -> None:
    contents = (
        "# Decisions\n\n"
        "## Baseline\n\n"
        f"- total_score: `{summary['train_score'] + summary['holdout_score']}` / "
        f"`{summary['train_max_score'] + summary['holdout_max_score']}`\n"
        f"- pass_rate: `{pass_rate}%`\n"
        f"- summary: {summary['summary']}\n\n"
        "## Mutation Log\n\n"
        "No experiments recorded yet.\n"
    )
    path.write_text(contents, encoding="utf-8")


def _write_text(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
