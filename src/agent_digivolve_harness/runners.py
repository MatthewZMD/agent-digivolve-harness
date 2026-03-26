from __future__ import annotations

import json
from pathlib import Path

from .agent_prompts import (
    build_case_agent_prompt,
    build_case_evaluator_prompt,
    build_case_evaluator_prompts,
    build_case_execution_steps,
    build_runner_agent_prompt,
    build_runner_execution_steps,
)
from .coordination import load_events, summarize_standing_user_instructions
from .evaluation import (
    calibration_file,
    excerpt_text,
    format_calibration_examples,
    load_calibration_examples,
    load_check_ids,
    load_checks,
    load_support_text,
    rubric_file,
)


def build_runner_payload(
    run_dir: Path,
    spec: dict,
    manifest: dict,
    *,
    kind: str,
    target_path: Path | None = None,
    worktree_path: Path | None = None,
    parent_commit: str | None = None,
) -> dict:
    artifact_type = spec["artifact_type"]
    adapter = _adapter_name(artifact_type)
    artifact_path = _target_object_path(spec, target_path)
    repository_path = _repository_path(spec, worktree_path)
    experiment_id = manifest["experiment_id"]
    exp_name = f"exp-{experiment_id:03d}"
    summary_path = str((run_dir / manifest["summary_file"]).resolve())
    checks_path = str((run_dir / spec["evaluation"]["checks_file"]).resolve())
    judge_path = str((run_dir / spec["evaluation"]["judge_file"]).resolve())
    rubric_path = str((run_dir / rubric_file(spec)).resolve())
    calibration_path = str((run_dir / calibration_file(spec)).resolve())
    checks = load_checks(run_dir / spec["evaluation"]["checks_file"])
    check_ids = load_check_ids(run_dir / spec["evaluation"]["checks_file"])
    rubric_text = load_support_text(run_dir / rubric_file(spec))
    calibration_examples = load_calibration_examples(run_dir / calibration_file(spec), limit=3)
    evaluation_contract = _evaluation_contract(spec)
    workspace = _workspace_payload(
        run_dir,
        exp_name,
        spec,
        artifact_path=artifact_path,
        repository_path=repository_path,
        worktree_path=worktree_path,
        parent_commit=parent_commit,
        kind=kind,
    )
    standing_user_instructions = summarize_standing_user_instructions(load_events(run_dir, limit=10))

    payload = {
        "run_dir": str(run_dir.resolve()),
        "adapter": adapter,
        "artifact_type": artifact_type,
        "experiment_kind": kind,
        "experiment_id": experiment_id,
        "artifact_path": artifact_path,
        "repository_path": repository_path,
        "checks_path": checks_path,
        "judge_path": judge_path,
        "rubric_path": rubric_path,
        "calibration_path": calibration_path,
        "checks": checks,
        "rubric_text": excerpt_text(rubric_text, limit=2000),
        "calibration_examples": calibration_examples,
        "calibration_summary": format_calibration_examples(calibration_examples),
        "summary_path": summary_path,
        "check_ids": check_ids,
        "per_case_max_score": len(check_ids),
        "evaluation_units": checks,
        "evaluation_contract": evaluation_contract,
        "standing_user_instructions": standing_user_instructions,
        "workspace": workspace,
        "mutation_scope": spec.get("mutation_scope", {}),
        "frozen_rules": spec.get("constraints", {}).get("frozen_rules", []),
        "instructions": _instructions(
            artifact_type,
            artifact_path=artifact_path,
            repository_path=repository_path,
            kind=kind,
            evaluation_contract=evaluation_contract,
        ),
        "case_contract": {
            "manifest_path": str((run_dir / f"outputs/{exp_name}/manifest.json").resolve()),
            "summary_path": summary_path,
            "check_ids": check_ids,
            "per_case_max_score": len(check_ids),
            "case_brief_dir": str((run_dir / f"outputs/{exp_name}/cases").resolve()),
            "output_rule": _output_rule(artifact_type),
            "score_rule": (
                "Only write the official score JSON after independent evaluator verdicts are recorded and aggregated."
                if evaluation_contract["independent_required"]
                else "Write one score JSON per case at the manifest `score_file` path."
            ),
            "brief_rule": "Read the per-case brief before executing each case.",
            "summary_rule": "Fill the experiment summary JSON only after all case files are complete.",
        },
    }
    payload["execution_steps"] = build_runner_execution_steps(payload)
    payload["agent_prompt"] = build_runner_agent_prompt(payload)
    return payload


def write_runner_brief(
    run_dir: Path,
    spec: dict,
    manifest: dict,
    *,
    kind: str,
    output_dir: Path,
    target_path: Path | None = None,
    worktree_path: Path | None = None,
    parent_commit: str | None = None,
) -> tuple[Path, Path]:
    payload = build_runner_payload(
        run_dir,
        spec,
        manifest,
        kind=kind,
        target_path=target_path,
        worktree_path=worktree_path,
        parent_commit=parent_commit,
    )
    json_path = output_dir / "runner.json"
    md_path = output_dir / "runner.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_render_runner_markdown(payload), encoding="utf-8")
    return json_path, md_path


def write_case_briefs(
    run_dir: Path,
    spec: dict,
    manifest: dict,
    *,
    kind: str,
    output_dir: Path,
    target_path: Path | None = None,
    worktree_path: Path | None = None,
    parent_commit: str | None = None,
) -> list[Path]:
    payload = build_runner_payload(
        run_dir,
        spec,
        manifest,
        kind=kind,
        target_path=target_path,
        worktree_path=worktree_path,
        parent_commit=parent_commit,
    )
    written: list[Path] = []
    for case in manifest["cases"]:
        brief_rel = case.get("brief_file")
        bundle_rel = case.get("bundle_file")
        if not brief_rel and not bundle_rel:
            continue
        case_payload = build_case_payload(payload, case)
        if brief_rel:
            brief_path = run_dir / brief_rel
            brief_path.parent.mkdir(parents=True, exist_ok=True)
            brief_path.write_text(_render_case_markdown(case_payload), encoding="utf-8")
            written.append(brief_path)
        if bundle_rel:
            bundle_path = run_dir / bundle_rel
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text(json.dumps(case_payload, indent=2) + "\n", encoding="utf-8")
            written.append(bundle_path)
    return written


def build_case_payload(runner_payload: dict, case: dict) -> dict:
    payload = {
        "run_dir": runner_payload["run_dir"],
        "experiment_id": runner_payload["experiment_id"],
        "experiment_kind": runner_payload["experiment_kind"],
        "adapter": runner_payload["adapter"],
        "artifact_type": runner_payload["artifact_type"],
        "artifact_path": runner_payload["artifact_path"],
        "repository_path": runner_payload["repository_path"],
        "checks_path": runner_payload["checks_path"],
        "judge_path": runner_payload["judge_path"],
        "rubric_path": runner_payload["rubric_path"],
        "calibration_path": runner_payload["calibration_path"],
        "checks": runner_payload["checks"],
        "rubric_text": runner_payload.get("rubric_text", ""),
        "calibration_examples": runner_payload.get("calibration_examples", []),
        "calibration_summary": runner_payload.get("calibration_summary", ""),
        "summary_path": runner_payload["summary_path"],
        "check_ids": runner_payload["check_ids"],
        "per_case_max_score": runner_payload["per_case_max_score"],
        "evaluation_units": runner_payload["evaluation_units"],
        "evaluation_contract": runner_payload["evaluation_contract"],
        "standing_user_instructions": runner_payload.get("standing_user_instructions", []),
        "mutation_scope": runner_payload["mutation_scope"],
        "frozen_rules": runner_payload["frozen_rules"],
        "workspace": runner_payload["workspace"],
        "case": {
            "split": case["split"],
            "id": case["id"],
            "kind": case.get("kind", _default_case_kind(runner_payload["artifact_type"])),
            "selector": case.get("selector"),
            "projection": case.get("projection"),
            "input": case["input"],
            "output_file": case["output_file"],
            "score_file": case["score_file"],
            "brief_file": case.get("brief_file"),
            "bundle_file": case.get("bundle_file"),
            "evaluation_dir": str(_evaluation_dir(runner_payload["workspace"]["output_dir"], case).resolve()),
        },
        "instructions": runner_payload["instructions"],
    }
    payload["execution_steps"] = build_case_execution_steps(payload)
    payload["agent_prompt"] = build_case_agent_prompt(payload)
    payload["evaluator_prompt"] = build_case_evaluator_prompt(payload)
    payload["evaluator_prompts"] = build_case_evaluator_prompts(payload)
    return payload


def _adapter_name(artifact_type: str) -> str:
    mapping = {
        "prompt": "prompt_runner",
        "document-copy": "document_copy_runner",
        "repo-task": "repo_task_runner",
    }
    return mapping[artifact_type]


def _target_object_path(spec: dict, target_path: Path | None) -> str | None:
    if target_path is not None:
        return str(target_path.resolve())
    return spec.get("target", {}).get("object_path")


def _repository_path(spec: dict, worktree_path: Path | None) -> str | None:
    if worktree_path is not None:
        return str(worktree_path.resolve())
    return spec.get("target", {}).get("repo_root")


def _output_rule(artifact_type: str) -> str:
    if artifact_type == "document-copy":
        return (
            "Write one per-case evidence file at the manifest `output_file` path. "
            "For `artifact_case`, capture the relevant README view as-is. For `section_case`, capture the selected section. "
            "Only `projection_case` should produce a derived answer grounded in the README."
        )
    return "Write one raw output file per case at the manifest `output_file` path."


def _default_case_kind(artifact_type: str) -> str:
    if artifact_type == "document-copy":
        return "projection_case"
    return "execution_case"


def _instructions(
    artifact_type: str,
    *,
    artifact_path: str | None,
    repository_path: str | None,
    kind: str,
    evaluation_contract: dict,
) -> list[str]:
    common = [
        "Read the manifest and treat it as the source of truth for case execution.",
        "Read the checks file, judge prompt, rubric, and calibration examples before delegating evaluation.",
        "Fill every case output and score file before finalizing the experiment.",
        "Keep execution deterministic and consistent across train and holdout cases.",
    ]
    if evaluation_contract["independent_required"]:
        common.append(
            f"Official scoring requires {evaluation_contract['panel_size']} independent evaluator verdict(s) via `{evaluation_contract['mode']}`."
        )

    if artifact_type == "prompt":
        return common + [
            f"Use the prompt artifact at `{artifact_path}` as the prompt under evaluation.",
            "For each case, apply the prompt to the case input and write the raw model response to the output file.",
            "Do not self-score the response. Hand it to an independent evaluator after writing the raw output.",
            f"This is a `{kind}` run. Only evaluate committed prompt state and only mutate when the phase explicitly allows it.",
        ]
    if artifact_type == "document-copy":
        return common + [
            f"Use the copy artifact at `{artifact_path}` as the canonical draft under evaluation.",
            "Treat document-copy cases as evaluation views over the same README, not as separate source-of-truth rewrites.",
            "For `artifact_case`, capture the relevant artifact view as-is. For `section_case`, capture the selected section from the README. Only `projection_case` should produce a derived answer grounded in the README.",
            "Do not self-score the copy. Hand the evidence or projection output to an independent evaluator after writing the raw output.",
            f"This is a `{kind}` run. Keep product facts and frozen constraints intact, and only evaluate committed target state.",
        ]
    return common + [
        f"Operate in the repository workspace `{repository_path or '<current workspace>'}`.",
        "Treat each case input as a concrete task or issue statement to execute in the repository.",
        "Write a concise execution result, changed files, and verification notes to the output file for each case.",
        "Do not self-score the case. Hand it to an independent evaluator after writing the raw output.",
        f"This is a `{kind}` run. Respect repository boundaries and frozen rules.",
    ]


def _render_runner_markdown(payload: dict) -> str:
    instructions = "\n".join(f"- {item}" for item in payload["instructions"])
    lines = [
        "# Runner Brief",
        "",
        f"- run_dir: `{payload['run_dir']}`",
        f"- adapter: `{payload['adapter']}`",
        f"- artifact_type: `{payload['artifact_type']}`",
        f"- experiment_kind: `{payload['experiment_kind']}`",
        f"- experiment_id: `{payload['experiment_id']}`",
    ]
    if payload["artifact_path"]:
        lines.append(f"- artifact_path: `{payload['artifact_path']}`")
    if payload["repository_path"]:
        lines.append(f"- repository_path: `{payload['repository_path']}`")

    lines += [
        f"- checks_path: `{payload['checks_path']}`",
        f"- judge_path: `{payload['judge_path']}`",
        f"- rubric_path: `{payload['rubric_path']}`",
        f"- calibration_path: `{payload['calibration_path']}`",
        f"- summary_path: `{payload['summary_path']}`",
        f"- check_ids: `{payload['check_ids']}`",
        f"- per_case_max_score: `{payload['per_case_max_score']}`",
        "",
        "## Evaluation Contract",
        "",
        f"- mode: `{payload['evaluation_contract']['mode']}`",
        f"- independent_required: `{payload['evaluation_contract']['independent_required']}`",
        f"- panel_size: `{payload['evaluation_contract']['panel_size']}`",
        f"- unit_isolation: `{payload['evaluation_contract']['unit_isolation']}`",
        "",
        "## Workspace",
        "",
        f"- output_dir: `{payload['workspace']['output_dir']}`",
        f"- score_dir: `{payload['workspace']['score_dir']}`",
    ]
    if payload["evaluation_contract"]["mode"] == "subagent":
        lines.insert(
            lines.index(f"- panel_size: `{payload['evaluation_contract']['panel_size']}`"),
            f"- subagent_model_policy: `{payload['evaluation_contract']['subagent_model_policy']}`",
        )
    if payload["workspace"].get("worktree_path"):
        lines.append(f"- worktree_path: `{payload['workspace']['worktree_path']}`")
    if payload["workspace"].get("target_path"):
        lines.append(f"- target_path: `{payload['workspace']['target_path']}`")
    if payload["workspace"].get("parent_commit"):
        lines.append(f"- parent_commit: `{payload['workspace']['parent_commit']}`")

    lines += [
        "",
        "## Constraints",
        "",
        f"- mutation_scope: `{payload['mutation_scope']}`",
    ]
    if payload["frozen_rules"]:
        lines.append("- frozen_rules:")
        lines.extend(f"  - {rule}" for rule in payload["frozen_rules"])

    lines += [
        "",
        "## Instructions",
        "",
        instructions,
        "",
        "## Execution Steps",
        "",
        *[f"- {item}" for item in payload["execution_steps"]],
        "",
        "## Case Contract",
        "",
        f"- manifest_path: `{payload['case_contract']['manifest_path']}`",
        f"- summary_path: `{payload['case_contract']['summary_path']}`",
        f"- check_ids: `{payload['case_contract']['check_ids']}`",
        f"- per_case_max_score: `{payload['case_contract']['per_case_max_score']}`",
        f"- case_brief_dir: `{payload['case_contract']['case_brief_dir']}`",
        f"- output_rule: {payload['case_contract']['output_rule']}",
        f"- score_rule: {payload['case_contract']['score_rule']}",
        f"- brief_rule: {payload['case_contract']['brief_rule']}",
        f"- summary_rule: {payload['case_contract']['summary_rule']}",
        "",
        "## Agent Prompt",
        "",
        "```text",
        payload["agent_prompt"],
        "```",
    ]
    return "\n".join(lines) + "\n"


def _workspace_payload(
    run_dir: Path,
    exp_name: str,
    spec: dict,
    *,
    artifact_path: str | None,
    repository_path: str | None,
    worktree_path: Path | None,
    parent_commit: str | None,
    kind: str,
) -> dict:
    payload = {
        "output_dir": str((run_dir / "outputs" / exp_name).resolve()),
        "score_dir": str((run_dir / "scores" / exp_name).resolve()),
        "target_path": artifact_path,
        "target_repo_root": spec.get("target", {}).get("repo_root"),
    }
    if repository_path:
        payload["repository_path"] = repository_path
    if kind == "step" and worktree_path is not None:
        payload["worktree_path"] = str(worktree_path.resolve())
    if parent_commit:
        payload["parent_commit"] = parent_commit
    return payload


def _render_case_markdown(payload: dict) -> str:
    case = payload["case"]
    lines = [
        "# Case Brief",
        "",
        f"- run_dir: `{payload['run_dir']}`",
        f"- experiment_id: `{payload['experiment_id']}`",
        f"- experiment_kind: `{payload['experiment_kind']}`",
        f"- split: `{case['split']}`",
        f"- id: `{case['id']}`",
        f"- adapter: `{payload['adapter']}`",
        f"- case_kind: `{case['kind']}`",
        f"- output_file: `{case['output_file']}`",
        f"- score_file: `{case['score_file']}`",
        f"- evaluation_dir: `{case['evaluation_dir']}`",
    ]
    if case.get("selector"):
        lines.append(f"- selector: `{case['selector']}`")
    if case.get("projection"):
        lines.append(f"- projection: `{case['projection']}`")
    if case.get("brief_file"):
        lines.append(f"- brief_file: `{case['brief_file']}`")
    if case.get("bundle_file"):
        lines.append(f"- bundle_file: `{case['bundle_file']}`")
    if payload["artifact_path"]:
        lines.append(f"- artifact_path: `{payload['artifact_path']}`")
    if payload["workspace"].get("target_path"):
        lines.append(f"- target_path: `{payload['workspace']['target_path']}`")
    if payload["workspace"].get("worktree_path"):
        lines.append(f"- worktree_path: `{payload['workspace']['worktree_path']}`")

    lines += [
        "",
        "## Case Input",
        "",
        "```text",
        case["input"],
        "```",
        "",
        "## Scoring",
        "",
        f"- checks_path: `{payload['checks_path']}`",
        f"- judge_path: `{payload['judge_path']}`",
        f"- rubric_path: `{payload['rubric_path']}`",
        f"- calibration_path: `{payload['calibration_path']}`",
        f"- check_ids: `{payload['check_ids']}`",
        f"- max_score: `{payload['per_case_max_score']}`",
        f"- evaluation_mode: `{payload['evaluation_contract']['mode']}`",
        f"- required_evaluators: `{payload['evaluation_contract']['panel_size']}`",
        f"- unit_isolation: `{payload['evaluation_contract']['unit_isolation']}`",
        "",
        "## Evaluation Units",
        "",
        *[
            f"- `{item['id']}`: {item['question']}"
            for item in payload["evaluation_units"]
        ],
        "",
        "## Execution Rules",
        "",
        *[f"- {item}" for item in payload["instructions"]],
        "",
        "## Execution Steps",
        "",
        *[f"- {item}" for item in payload["execution_steps"]],
        "",
        "## Deliverables",
        "",
        f"- Write the raw result to `{case['output_file']}`.",
        f"- Record independent evaluator verdicts in `{case['evaluation_dir']}`.",
        f"- Finalize the official score JSON at `{case['score_file']}` only after enough verdicts exist.",
        f"- Keep notes concise and make sure the summary file `{payload['summary_path']}` is filled only after all cases are complete.",
        "",
        "## Agent Prompt",
        "",
        "```text",
        payload["agent_prompt"],
        "```",
        "",
        "## Evaluator Prompt",
        "",
        "```text",
        payload["evaluator_prompt"],
        "```",
        "",
        "## Per-Check Evaluator Prompts",
        "",
        *[
            section
            for unit in payload["evaluation_units"]
            for section in (
                f"### `{unit['id']}`",
                "",
                "```text",
                payload["evaluator_prompts"][unit["id"]],
                "```",
                "",
            )
        ],
    ]
    if payload["evaluation_contract"]["mode"] == "subagent":
        lines.insert(
            lines.index(f"- required_evaluators: `{payload['evaluation_contract']['panel_size']}`"),
            f"- subagent_model_policy: `{payload['evaluation_contract']['subagent_model_policy']}`",
        )
    return "\n".join(lines) + "\n"


def _evaluation_contract(spec: dict) -> dict:
    evaluation = spec.get("evaluation", {})
    return {
        "mode": evaluation.get("evaluator_mode", "subagent"),
        "subagent_system": evaluation.get("subagent_system", "codex"),
        "subagent_model_policy": evaluation.get("subagent_model_policy", "best_available"),
        "independent_required": bool(evaluation.get("require_independent_evaluator", False)),
        "panel_size": max(1, int(evaluation.get("panel_size", 1))),
        "external_agents": list(evaluation.get("external_agents", [])),
        "unit_isolation": "per_check",
        "consensus_rule": "majority_vote_per_check",
    }


def _evaluation_dir(output_dir: str, case: dict) -> Path:
    exp_dir = Path(output_dir).resolve().name
    return Path(output_dir).resolve().parents[1] / "evaluations" / exp_dir / f"{case['split']}-{case['id']}"
