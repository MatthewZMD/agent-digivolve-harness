from __future__ import annotations

import json
from pathlib import Path

from .evaluation import load_check_ids
from .workspace import load_run_spec


OUTPUT_PLACEHOLDER = "<write raw output here>\n"


def initialize_case_artifacts(run_dir: Path, manifest: dict) -> None:
    check_ids = load_check_ids(run_dir / "evals" / "checks.yaml")
    for case in manifest["cases"]:
        output_path = run_dir / case["output_file"]
        score_path = run_dir / case["score_file"]
        verdict_dir = _evaluation_dir(run_dir, case)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        score_path.parent.mkdir(parents=True, exist_ok=True)
        verdict_dir.mkdir(parents=True, exist_ok=True)

        if not output_path.exists():
            output_path.write_text(OUTPUT_PLACEHOLDER, encoding="utf-8")
        if not score_path.exists():
            score_path.write_text(
                json.dumps(_score_template(case, check_ids), indent=2) + "\n",
                encoding="utf-8",
            )


def collect_case_results(run_dir: Path, experiment_id: int) -> dict:
    exp_name = f"exp-{experiment_id:03d}"
    manifest_path = run_dir / "outputs" / exp_name / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    cases: list[dict] = []
    train_score = 0.0
    train_max_score = 0.0
    holdout_score = 0.0
    holdout_max_score = 0.0

    for case in manifest["cases"]:
        loaded = load_case_result(run_dir, case)
        output_path = run_dir / case["output_file"]
        score_path = run_dir / case["score_file"]
        output_text = loaded["output_text"]
        score_payload = loaded["score_payload"]

        cases.append(
            {
                "split": case["split"],
                "id": case["id"],
                "input": case["input"],
                "output_file": str(output_path.resolve()),
                "score_file": str(score_path.resolve()),
                "output_preview": output_text[:200],
                "score": score_payload["score"],
                "max_score": score_payload["max_score"],
                "passed_checks": score_payload["passed_checks"],
                "failed_checks": score_payload["failed_checks"],
                "notes": score_payload["notes"],
            }
        )

        if case["split"] == "train":
            train_score += score_payload["score"]
            train_max_score += score_payload["max_score"]
        else:
            holdout_score += score_payload["score"]
            holdout_max_score += score_payload["max_score"]

    return {
        "experiment_id": experiment_id,
        "cases": cases,
        "train_score": train_score,
        "train_max_score": train_max_score,
        "holdout_score": holdout_score,
        "holdout_max_score": holdout_max_score,
    }


def load_case_result(run_dir: Path, case: dict) -> dict:
    output_path = run_dir / case["output_file"]
    score_path = run_dir / case["score_file"]
    output_text = _load_output(output_path)
    score_payload = _load_case_score(score_path, case)
    return {
        "output_text": output_text,
        "score_payload": score_payload,
        "output_path": str(output_path.resolve()),
        "score_path": str(score_path.resolve()),
    }


def inspect_case_artifacts(run_dir: Path, case: dict) -> dict:
    output_path = run_dir / case["output_file"]
    score_path = run_dir / case["score_file"]

    output_status = _file_status(output_path, placeholder=OUTPUT_PLACEHOLDER.strip())
    score_status = _score_file_status(score_path)
    evaluations_status = _evaluations_status(run_dir, case)

    ready = output_status["ready"] and score_status["ready"]
    payload = {
        "ready": ready,
        "output": output_status,
        "score": score_status,
    }
    if evaluations_status is not None:
        payload["evaluations"] = evaluations_status
    return payload


def _score_template(case: dict, check_ids: list[str]) -> dict:
    return {
        "split": case["split"],
        "id": case["id"],
        "score": 0,
        "max_score": len(check_ids),
        "available_checks": check_ids,
        "passed_checks": [],
        "failed_checks": [],
        "notes": "Replace this with concise scoring notes.",
    }


def _load_output(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing case output file: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text or text == OUTPUT_PLACEHOLDER.strip():
        raise ValueError(f"Case output is empty or still placeholder: {path}")
    return text


def _load_case_score(path: Path, case: dict) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing case score file: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    required_fields = ["score", "max_score", "passed_checks", "failed_checks", "notes"]
    missing = [field for field in required_fields if field not in payload]
    if missing:
        raise ValueError(f"Case score file is missing fields {missing}: {path}")

    score = payload["score"]
    max_score = payload["max_score"]
    if not isinstance(score, (int, float)) or not isinstance(max_score, (int, float)):
        raise ValueError(f"Case score values must be numeric: {path}")
    if score < 0 or max_score <= 0 or score > max_score:
        raise ValueError(f"Invalid case score range in {path}")

    passed_checks = payload["passed_checks"]
    failed_checks = payload["failed_checks"]
    if not isinstance(passed_checks, list) or not isinstance(failed_checks, list):
        raise ValueError(f"Case checks must be lists in {path}")

    notes = payload["notes"]
    if not isinstance(notes, str) or not notes.strip() or notes.strip() == "Replace this with concise scoring notes.":
        raise ValueError(f"Case score notes must be filled in {path}")

    if payload.get("split") and payload["split"] != case["split"]:
        raise ValueError(f"Case split mismatch in {path}")
    if payload.get("id") and payload["id"] != case["id"]:
        raise ValueError(f"Case id mismatch in {path}")

    return {
        "score": float(score),
        "max_score": float(max_score),
        "passed_checks": passed_checks,
        "failed_checks": failed_checks,
        "notes": notes.strip(),
    }


def _file_status(path: Path, *, placeholder: str) -> dict:
    if not path.exists():
        return {"ready": False, "state": "missing", "reason": "file missing"}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"ready": False, "state": "empty", "reason": "file is empty"}
    if text == placeholder:
        return {"ready": False, "state": "placeholder", "reason": "file is still placeholder"}
    return {"ready": True, "state": "ready", "reason": "ready"}


def _score_file_status(path: Path) -> dict:
    if not path.exists():
        return {"ready": False, "state": "missing", "reason": "file missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"ready": False, "state": "invalid", "reason": "score file is not valid JSON"}

    notes = payload.get("notes")
    max_score = payload.get("max_score")
    if not isinstance(max_score, (int, float)) or max_score <= 0:
        return {"ready": False, "state": "placeholder", "reason": "max_score is not initialized"}
    if not isinstance(notes, str) or not notes.strip() or notes.strip() == "Replace this with concise scoring notes.":
        return {"ready": False, "state": "placeholder", "reason": "score notes are still placeholder"}
    return {"ready": True, "state": "ready", "reason": "ready"}


def _evaluations_status(run_dir: Path, case: dict) -> dict | None:
    spec = load_run_spec(run_dir)
    evaluation = spec.get("evaluation", {})
    if not evaluation.get("require_independent_evaluator", False):
        return None

    verdict_dir = _evaluation_dir(run_dir, case)
    check_ids = load_check_ids(run_dir / "evals" / "checks.yaml")
    required_per_check = max(1, int(evaluation.get("panel_size", 1)))
    recorded_files = _verdict_files(verdict_dir) if verdict_dir.exists() else []
    recorded = len(recorded_files)
    by_check: dict[str, int] = {check_id: 0 for check_id in check_ids}
    for path in recorded_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        check_id = payload.get("check_id")
        if check_id in by_check:
            by_check[check_id] += 1
    return {
        "mode": evaluation.get("evaluator_mode", "subagent"),
        "recorded": recorded,
        "required": required_per_check * len(check_ids),
        "required_per_check": required_per_check,
        "ready": all(count >= required_per_check for count in by_check.values()),
        "check_statuses": {
            check_id: {
                "recorded": by_check[check_id],
                "required": required_per_check,
                "ready": by_check[check_id] >= required_per_check,
            }
            for check_id in check_ids
        },
        "dir": str(verdict_dir.resolve()),
    }


def _evaluation_dir(run_dir: Path, case: dict) -> Path:
    score_path = Path(case["score_file"])
    exp_name = score_path.parent.name
    return run_dir / "evaluations" / exp_name / f"{case['split']}-{case['id']}"


def _verdict_files(verdict_dir: Path) -> list[Path]:
    return [
        path
        for path in sorted(verdict_dir.glob("*.json"))
        if not path.name.endswith(".trace.json")
    ]
