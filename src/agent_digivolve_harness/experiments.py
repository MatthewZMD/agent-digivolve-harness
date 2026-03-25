from __future__ import annotations

import json
from pathlib import Path

from .baseline import finalize_baseline
from .execution import list_cases, load_runner, resolve_experiment_id
from .step import finalize_step
from .workspace import load_json, load_run_state, resolve_run_dir


def experiment_status(run_dir: Path, *, experiment_id: int | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    state = load_run_state(run_dir)
    active = _active_experiment(state)
    resolved_id = resolve_experiment_id(run_dir, experiment_id=experiment_id)
    manifest = _load_manifest(run_dir, resolved_id)
    runner = load_runner(run_dir, experiment_id=resolved_id)
    cases_payload = list_cases(run_dir, experiment_id=resolved_id)
    summary = _summary_status(run_dir, manifest)

    ready_cases = [case for case in cases_payload["cases"] if case["status"]["ready"]]
    pending_cases = [case for case in cases_payload["cases"] if not case["status"]["ready"]]

    return {
        "run_dir": str(run_dir),
        "experiment_id": resolved_id,
        "kind": manifest["kind"],
        "state_status": state.get("status", "draft"),
        "active": bool(active and active["experiment_id"] == resolved_id),
        "adapter": runner["adapter"],
        "runner_path": runner["runner_path"],
        "manifest_path": str((run_dir / f"outputs/exp-{resolved_id:03d}/manifest.json").resolve()),
        "summary_path": str((run_dir / manifest["summary_file"]).resolve()),
        "total_cases": len(cases_payload["cases"]),
        "ready_cases": len(ready_cases),
        "pending_cases": len(pending_cases),
        "pending_case_ids": [f"{case['split']}:{case['id']}" for case in pending_cases],
        "cases_ready": len(pending_cases) == 0,
        "summary_ready": summary["ready"],
        "summary_status": summary,
        "finalizable": len(pending_cases) == 0 and summary["ready"],
        "cases": cases_payload["cases"],
    }


def complete_experiment(run_dir: Path, *, experiment_id: int | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    state = load_run_state(run_dir)
    active = _active_experiment(state)
    if active is None:
        return {
            "run_dir": str(run_dir),
            "completed": False,
            "reason": "no in-progress experiment to complete",
        }

    resolved_id = active["experiment_id"] if experiment_id is None else int(experiment_id)
    if resolved_id != active["experiment_id"]:
        return {
            "run_dir": str(run_dir),
            "experiment_id": resolved_id,
            "completed": False,
            "reason": "selected experiment is not the active in-progress experiment",
        }

    status = experiment_status(run_dir, experiment_id=resolved_id)
    if not status["finalizable"]:
        return {
            "run_dir": str(run_dir),
            "experiment_id": status["experiment_id"],
            "kind": status["kind"],
            "completed": False,
            "reason": "experiment is not ready to finalize",
            "status": status,
        }

    if status["kind"] == "baseline":
        payload = finalize_baseline(run_dir)
    elif status["kind"] == "step":
        payload = finalize_step(run_dir)
    else:
        raise ValueError(f"Unsupported experiment kind: {status['kind']}")

    return {
        "run_dir": str(run_dir),
        "experiment_id": status["experiment_id"],
        "kind": status["kind"],
        "completed": True,
        "result": payload,
    }


def _load_manifest(run_dir: Path, experiment_id: int) -> dict:
    manifest_path = run_dir / "outputs" / f"exp-{experiment_id:03d}" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return load_json(manifest_path)


def _summary_status(run_dir: Path, manifest: dict) -> dict:
    path = run_dir / manifest["summary_file"]
    if not path.exists():
        return {"ready": False, "state": "missing", "reason": "summary file missing"}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"ready": False, "state": "invalid", "reason": "summary file is not valid JSON"}

    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip() or summary.strip().startswith("Replace this with"):
        return {"ready": False, "state": "placeholder", "reason": "summary text is still placeholder"}

    if manifest["kind"] == "step":
        mutation_description = payload.get("mutation_description")
        if (
            not isinstance(mutation_description, str)
            or not mutation_description.strip()
            or mutation_description.strip().startswith("Replace this with")
        ):
            return {
                "ready": False,
                "state": "placeholder",
                "reason": "mutation_description is still placeholder",
            }

    return {"ready": True, "state": "ready", "reason": "ready"}


def _active_experiment(state: dict) -> dict | None:
    status = state.get("status")
    if status == "baseline_in_progress":
        return {"experiment_id": 0, "kind": "baseline"}
    if status == "step_in_progress" and state.get("pending_experiment") is not None:
        return {
            "experiment_id": int(state["pending_experiment"]),
            "kind": "step",
        }
    return None
