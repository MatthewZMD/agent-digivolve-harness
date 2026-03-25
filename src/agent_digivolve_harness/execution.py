from __future__ import annotations

import json
from pathlib import Path

from .casefiles import collect_case_results, inspect_case_artifacts, load_case_result
from .agent_prompts import (
    build_runner_agent_prompt,
    build_runner_execution_steps,
)
from .runners import build_case_payload
from .workspace import load_json, load_run_spec, load_run_state, resolve_run_dir, write_json


def load_runner(run_dir: Path, *, experiment_id: int | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    runner_path = run_dir / "outputs" / f"exp-{resolved_id:03d}" / "runner.json"
    if not runner_path.exists():
        raise FileNotFoundError(f"Runner payload not found: {runner_path}")
    payload = load_json(runner_path)
    if "execution_steps" not in payload:
        payload["execution_steps"] = build_runner_execution_steps(payload)
    if "agent_prompt" not in payload:
        raise ValueError(f"Runner payload is missing required `agent_prompt`: {runner_path}")
    payload["runner_path"] = str(runner_path.resolve())
    return payload


def list_cases(run_dir: Path, *, experiment_id: int | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    runner = load_runner(run_dir, experiment_id=resolved_id)

    cases = [_case_summary(run_dir, case) for case in manifest["cases"]]
    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "runner_path": runner["runner_path"],
        "adapter": runner["adapter"],
        "total_cases": len(cases),
        "cases": cases,
    }


def load_case(run_dir: Path, case_id: str, *, experiment_id: int | None = None, split: str | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    runner = load_runner(run_dir, experiment_id=resolved_id)
    case = _find_case(manifest, case_id, split=split)
    bundle = _load_case_bundle(run_dir, case, runner=runner)

    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "runner": runner,
        "case": bundle,
        "status": inspect_case_artifacts(run_dir, case),
    }


def validate_case(run_dir: Path, case_id: str, *, experiment_id: int | None = None, split: str | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    case = _find_case(manifest, case_id, split=split)

    try:
        loaded = load_case_result(run_dir, case)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "run_dir": str(run_dir),
            "experiment_id": resolved_id,
            "case_id": case_id,
            "split": case["split"],
            "valid": False,
            "reason": str(exc),
            "status": inspect_case_artifacts(run_dir, case),
        }

    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "case_id": case_id,
        "split": case["split"],
        "valid": True,
        "status": inspect_case_artifacts(run_dir, case),
        "result": {
            "output_path": loaded["output_path"],
            "score_path": loaded["score_path"],
            "score": loaded["score_payload"]["score"],
            "max_score": loaded["score_payload"]["max_score"],
            "passed_checks": loaded["score_payload"]["passed_checks"],
            "failed_checks": loaded["score_payload"]["failed_checks"],
            "notes": loaded["score_payload"]["notes"],
        },
    }


def record_case(
    run_dir: Path,
    case_id: str,
    *,
    output_text: str | None = None,
    output_file: str | None = None,
    score: float,
    notes: str,
    passed_checks: list[str] | None = None,
    failed_checks: list[str] | None = None,
    experiment_id: int | None = None,
    split: str | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    evaluation = _evaluation_settings(spec)
    if evaluation["independent_required"]:
        raise ValueError(
            "This run requires independent evaluator verdicts. "
            "Write the raw output, use `record-eval`, then `finalize-case`."
        )

    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    case = _find_case(manifest, case_id, split=split)
    bundle = _load_case_bundle(run_dir, case)

    available_checks = bundle.get("check_ids") or []
    max_score = bundle.get("per_case_max_score")
    if not isinstance(max_score, (int, float)) or max_score <= 0:
        raise ValueError("Case bundle is missing a valid per_case_max_score")

    resolved_output_text = _resolve_output_text(run_dir, output_text=output_text, output_file=output_file)
    payload = _build_score_payload(
        case=case,
        score=score,
        max_score=float(max_score),
        notes=notes,
        passed_checks=passed_checks or [],
        failed_checks=failed_checks or [],
        available_checks=available_checks,
    )

    output_path = run_dir / case["output_file"]
    score_path = run_dir / case["score_file"]
    output_path.write_text(_normalize_text(resolved_output_text), encoding="utf-8")
    write_json(score_path, payload)

    validation = validate_case(
        run_dir,
        case_id,
        experiment_id=resolved_id,
        split=case["split"],
    )
    validation["recorded"] = True
    return validation


def record_eval(
    run_dir: Path,
    case_id: str,
    *,
    evaluator_id: str,
    evaluator_kind: str,
    check_id: str,
    passed: bool,
    notes: str,
    model_name: str | None = None,
    evaluator_label: str | None = None,
    experiment_id: int | None = None,
    split: str | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    evaluation = _evaluation_settings(spec)
    if not evaluation["independent_required"]:
        raise ValueError("This run does not require independent evaluator verdicts.")

    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    case = _find_case(manifest, case_id, split=split)
    bundle = _load_case_bundle(run_dir, case)
    output_status = inspect_case_artifacts(run_dir, case)["output"]
    if not output_status["ready"]:
        raise ValueError("Case output must be written before an evaluator verdict can be recorded.")

    available_checks = bundle.get("check_ids") or []
    if check_id not in available_checks:
        raise ValueError(f"Unknown check referenced: {check_id}")

    payload = _build_score_payload(
        case=case,
        score=1.0 if passed else 0.0,
        max_score=1.0,
        notes=notes,
        passed_checks=[check_id] if passed else [],
        failed_checks=[] if passed else [check_id],
        available_checks=[check_id],
        require_complete_check_assignment=True,
    )
    payload["check_id"] = check_id
    payload["passed"] = bool(passed)
    payload["evaluator"] = {
        "id": evaluator_id.strip(),
        "kind": evaluator_kind.strip(),
        "label": evaluator_label.strip() if evaluator_label and evaluator_label.strip() else None,
        "model": model_name.strip() if model_name and model_name.strip() else None,
    }
    if not payload["evaluator"]["id"]:
        raise ValueError("evaluator_id must be non-empty.")
    if not payload["evaluator"]["kind"]:
        raise ValueError("evaluator_kind must be non-empty.")

    verdict_dir = _evaluation_dir(run_dir, resolved_id, case)
    verdict_dir.mkdir(parents=True, exist_ok=True)
    verdict_path = verdict_dir / _verdict_filename(check_id, payload["evaluator"]["id"])
    write_json(verdict_path, payload)

    verdicts = _load_verdicts(verdict_dir)
    statuses = _check_statuses(verdicts, available_checks, evaluation["panel_size"])
    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "case_id": case["id"],
        "split": case["split"],
        "check_id": check_id,
        "recorded": True,
        "verdict_path": str(verdict_path.resolve()),
        "required_evaluators_per_check": evaluation["panel_size"],
        "recorded_verdicts": len(verdicts),
        "check_statuses": statuses,
        "ready_to_finalize": all(item["ready"] for item in statuses.values()),
        "verdict": payload,
    }


def list_case_evaluations(
    run_dir: Path,
    case_id: str,
    *,
    experiment_id: int | None = None,
    split: str | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    evaluation = _evaluation_settings(spec)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    case = _find_case(manifest, case_id, split=split)
    verdict_dir = _evaluation_dir(run_dir, resolved_id, case)
    verdicts = _load_verdicts(verdict_dir)
    statuses = _check_statuses(verdicts, load_case(run_dir, case_id, experiment_id=resolved_id, split=case["split"])["case"]["check_ids"], evaluation["panel_size"])
    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "case_id": case["id"],
        "split": case["split"],
        "evaluation_mode": evaluation["mode"],
        "required_evaluators_per_check": evaluation["panel_size"],
        "recorded_verdicts": len(verdicts),
        "ready_to_finalize": all(item["ready"] for item in statuses.values()),
        "verdict_dir": str(verdict_dir.resolve()),
        "check_statuses": statuses,
        "verdicts": verdicts,
    }


def finalize_case(
    run_dir: Path,
    case_id: str,
    *,
    experiment_id: int | None = None,
    split: str | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    evaluation = _evaluation_settings(spec)
    if not evaluation["independent_required"]:
        raise ValueError("This run does not require independent evaluator verdict aggregation.")

    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    case = _find_case(manifest, case_id, split=split)
    output_status = inspect_case_artifacts(run_dir, case)["output"]
    if not output_status["ready"]:
        raise ValueError("Case output must be written before finalizing the official score.")

    verdict_dir = _evaluation_dir(run_dir, resolved_id, case)
    verdicts = _load_verdicts(verdict_dir)
    bundle = _load_case_bundle(run_dir, case)
    available_checks = bundle.get("check_ids") or []
    statuses = _check_statuses(verdicts, available_checks, evaluation["panel_size"])
    not_ready = [check for check, status in statuses.items() if not status["ready"]]
    if not_ready:
        raise ValueError(
            "Need isolated evaluator verdicts for every check before finalizing. Missing panel coverage for: "
            + ", ".join(not_ready)
        )

    aggregate = _aggregate_verdicts(verdicts, expected_check_ids=available_checks, panel_size=evaluation["panel_size"])
    score_path = run_dir / case["score_file"]
    write_json(score_path, aggregate)

    validation = validate_case(
        run_dir,
        case_id,
        experiment_id=resolved_id,
        split=case["split"],
    )
    validation["recorded"] = True
    validation["aggregated_from"] = len(verdicts)
    validation["check_statuses"] = statuses
    return validation


def record_summary(
    run_dir: Path,
    *,
    summary: str,
    experiment_id: int | None = None,
    mutation_description: str | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    kind = manifest["kind"]

    case_results = collect_case_results(run_dir, resolved_id)
    summary_path = run_dir / manifest["summary_file"]
    payload = {
        "train_score": case_results["train_score"],
        "train_max_score": case_results["train_max_score"],
        "holdout_score": case_results["holdout_score"],
        "holdout_max_score": case_results["holdout_max_score"],
        "summary": summary.strip(),
    }
    if not payload["summary"]:
        raise ValueError("summary must be non-empty.")
    if kind == "step":
        if not mutation_description or not mutation_description.strip():
            raise ValueError("mutation_description is required for step summaries.")
        payload["mutation_description"] = mutation_description.strip()

    write_json(summary_path, payload)
    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "kind": kind,
        "summary_path": str(summary_path.resolve()),
        "recorded": True,
        "summary": payload,
    }


def resolve_experiment_id(run_dir: Path, *, experiment_id: int | None = None) -> int:
    if experiment_id is not None:
        return int(experiment_id)

    state = load_run_state(run_dir)
    if state.get("pending_experiment") is not None:
        return int(state["pending_experiment"])
    if state.get("current_experiment", 0) > 0:
        return int(state["current_experiment"])
    if (run_dir / "outputs" / "exp-000" / "runner.json").exists():
        return 0
    raise ValueError("No active or materialized experiment is available yet.")


def _load_manifest(run_dir: Path, experiment_id: int) -> dict:
    manifest_path = run_dir / "outputs" / f"exp-{experiment_id:03d}" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return load_json(manifest_path)


def _find_case(manifest: dict, case_id: str, *, split: str | None = None) -> dict:
    matches = [
        case
        for case in manifest["cases"]
        if case["id"] == case_id and (split is None or case["split"] == split)
    ]
    if not matches:
        split_hint = f" in split `{split}`" if split else ""
        raise ValueError(f"Case `{case_id}` not found{split_hint}.")
    if len(matches) > 1:
        raise ValueError(f"Case `{case_id}` is ambiguous; specify --split.")
    return matches[0]


def _case_summary(run_dir: Path, case: dict) -> dict:
    status = inspect_case_artifacts(run_dir, case)
    payload = {
        "split": case["split"],
        "id": case["id"],
        "input_preview": case["input"][:120],
        "output_file": str((run_dir / case["output_file"]).resolve()),
        "score_file": str((run_dir / case["score_file"]).resolve()),
        "status": status,
    }
    if case.get("brief_file"):
        payload["brief_file"] = str((run_dir / case["brief_file"]).resolve())
    if case.get("bundle_file"):
        payload["bundle_file"] = str((run_dir / case["bundle_file"]).resolve())
    return payload


def _load_case_bundle(run_dir: Path, case: dict, *, runner: dict | None = None) -> dict:
    bundle_path = case.get("bundle_file")
    if bundle_path:
        bundle_file = run_dir / bundle_path
        if bundle_file.exists():
            payload = load_json(bundle_file)
            missing = [
                key for key in ("execution_steps", "agent_prompt") if key not in payload
            ]
            if missing:
                raise ValueError(
                    f"Case bundle is missing required field(s) {missing}: {bundle_file}"
                )
            payload["bundle_path"] = str(bundle_file.resolve())
            return payload

    payload = build_case_payload(runner, case) if runner is not None else {"case": case}
    if case.get("brief_file"):
        payload["brief_path"] = str((run_dir / case["brief_file"]).resolve())
    return payload


def _resolve_output_text(
    run_dir: Path,
    *,
    output_text: str | None,
    output_file: str | None,
) -> str:
    if output_text and output_file:
        raise ValueError("Use either output_text or output_file, not both.")
    if output_file:
        path = Path(output_file).expanduser()
        if not path.is_absolute():
            path = (run_dir / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Output source file not found: {path}")
        text = path.read_text(encoding="utf-8")
    elif output_text is not None:
        text = output_text
    else:
        raise ValueError("Either output_text or output_file is required.")

    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Recorded case output cannot be empty.")
    return cleaned


def _build_score_payload(
    *,
    case: dict,
    score: float,
    max_score: float,
    notes: str,
    passed_checks: list[str],
    failed_checks: list[str],
    available_checks: list[str],
    require_complete_check_assignment: bool = False,
) -> dict:
    if score < 0 or score > max_score:
        raise ValueError("score must be within [0, max_score].")
    if not notes.strip():
        raise ValueError("notes must be non-empty.")

    available = set(available_checks)
    passed = list(dict.fromkeys(passed_checks))
    failed = list(dict.fromkeys(failed_checks))

    unknown = sorted((set(passed) | set(failed)) - available)
    if unknown:
        raise ValueError(f"Unknown checks referenced: {', '.join(unknown)}")
    overlap = sorted(set(passed) & set(failed))
    if overlap:
        raise ValueError(f"Checks cannot be both passed and failed: {', '.join(overlap)}")
    if require_complete_check_assignment:
        missing = sorted(available - (set(passed) | set(failed)))
        if missing:
            raise ValueError(f"Each verdict must classify every check. Missing: {', '.join(missing)}")

    return {
        "split": case["split"],
        "id": case["id"],
        "score": float(score),
        "max_score": float(max_score),
        "available_checks": available_checks,
        "passed_checks": passed,
        "failed_checks": failed,
        "notes": notes.strip(),
    }


def _evaluation_settings(spec: dict) -> dict:
    evaluation = spec.get("evaluation", {})
    return {
        "mode": evaluation.get("evaluator_mode", "subagent"),
        "independent_required": bool(evaluation.get("require_independent_evaluator", False)),
        "panel_size": max(1, int(evaluation.get("panel_size", 1))),
    }


def _evaluation_dir(run_dir: Path, experiment_id: int, case: dict) -> Path:
    return run_dir / "evaluations" / f"exp-{experiment_id:03d}" / f"{case['split']}-{case['id']}"


def _load_verdicts(verdict_dir: Path) -> list[dict]:
    verdicts: list[dict] = []
    if not verdict_dir.exists():
        return verdicts
    for path in _verdict_files(verdict_dir):
        payload = load_json(path)
        payload["verdict_path"] = str(path.resolve())
        verdicts.append(payload)
    return verdicts


def _verdict_files(verdict_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(verdict_dir.glob("*.json"))
        if not path.name.endswith(".trace.json")
    ]


def _aggregate_verdicts(verdicts: list[dict], *, expected_check_ids: list[str], panel_size: int) -> dict:
    if not verdicts:
        raise ValueError("No evaluator verdicts were recorded.")

    first = verdicts[0]
    grouped = _group_verdicts_by_check(verdicts)
    check_panels = []
    passed_checks: list[str] = []
    failed_checks: list[str] = []

    for check_id in expected_check_ids:
        check_verdicts = grouped.get(check_id, [])
        if len(check_verdicts) < panel_size:
            raise ValueError(f"Check `{check_id}` does not have enough independent verdicts.")
        pass_votes = sum(1 for item in check_verdicts if item.get("passed_checks") == [check_id])
        passed = pass_votes > len(check_verdicts) / 2
        if passed:
            passed_checks.append(check_id)
        else:
            failed_checks.append(check_id)
        check_panels.append(
            {
                "check_id": check_id,
                "verdict_count": len(check_verdicts),
                "pass_votes": pass_votes,
                "failed_votes": len(check_verdicts) - pass_votes,
                "passed": passed,
                "verdicts": [
                    {
                        "id": item.get("evaluator", {}).get("id"),
                        "kind": item.get("evaluator", {}).get("kind"),
                        "label": item.get("evaluator", {}).get("label"),
                        "model": item.get("evaluator", {}).get("model"),
                        "passed": item.get("passed", False),
                        "notes": item.get("notes"),
                    }
                    for item in check_verdicts
                ],
            }
        )

    score = float(len(passed_checks))
    max_score = float(len(expected_check_ids))
    panel = [
        {
            "id": item.get("evaluator", {}).get("id"),
            "kind": item.get("evaluator", {}).get("kind"),
            "label": item.get("evaluator", {}).get("label"),
            "model": item.get("evaluator", {}).get("model"),
            "check_id": item.get("check_id"),
            "passed": item.get("passed", False),
            "notes": item.get("notes"),
        }
        for item in verdicts
    ]
    notes = "Aggregated from isolated evaluator verdicts: " + " | ".join(
        f"{entry['check_id']}:{entry['id']}={'pass' if entry['passed'] else 'fail'}: {entry['notes']}"
        for entry in panel
    )
    return {
        "split": first["split"],
        "id": first["id"],
        "score": score,
        "max_score": max_score,
        "passed_checks": passed_checks,
        "failed_checks": failed_checks,
        "notes": notes,
        "aggregation": {
            "method": "majority_vote_per_check",
            "verdict_count": len(verdicts),
            "required_evaluators_per_check": panel_size,
            "evaluator_mode": "independent_panel",
        },
        "evaluator_panel": panel,
        "check_panels": check_panels,
    }


def _group_verdicts_by_check(verdicts: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in verdicts:
        check_id = item.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            raise ValueError("Evaluator verdict is missing `check_id`.")
        grouped.setdefault(check_id, []).append(item)
    return grouped


def _check_statuses(verdicts: list[dict], check_ids: list[str], required_per_check: int) -> dict[str, dict]:
    grouped = _group_verdicts_by_check(verdicts) if verdicts else {}
    statuses: dict[str, dict] = {}
    for check_id in check_ids:
        count = len(grouped.get(check_id, []))
        statuses[check_id] = {
            "recorded": count,
            "required": required_per_check,
            "ready": count >= required_per_check,
        }
    return statuses


def _verdict_filename(check_id: str, evaluator_id: str) -> str:
    return f"{_safe_token(check_id)}--{_safe_token(evaluator_id)}.json"


def _safe_token(value: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return slug or "token"


def _normalize_text(text: str) -> str:
    return text.rstrip() + "\n"
