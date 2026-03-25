from __future__ import annotations

from pathlib import Path

from .baseline import finalize_baseline, prepare_baseline
from .drafting import draft_evals
from .reporting import generate_report
from .resume import build_resume_payload
from .runtime import build_next_payload
from .step import finalize_step, prepare_step
from .workspace import resolve_run_dir


def advance_run(run_dir: Path, *, activate_paused: bool = False, resume_limit: int = 3) -> dict:
    run_dir = resolve_run_dir(run_dir)
    next_payload = build_next_payload(run_dir)
    phase = next_payload["phase"]

    if phase == "draft":
        payload = draft_evals(run_dir)
    elif phase == "awaiting_confirmation":
        payload = {
            "run_dir": str(run_dir),
            "phase": phase,
            "state_status": phase,
            "blocked": True,
            "next_action": next_payload["next_action"],
            "reason": "User confirmation is required before baseline can start.",
            "required_reads": next_payload["required_reads"],
            "allowed_writes": next_payload["allowed_writes"],
            "success_condition": next_payload["success_condition"],
        }
    elif phase == "ready":
        payload = prepare_baseline(run_dir)
    elif phase == "baseline_in_progress":
        payload = _finalize_or_block(
            run_dir,
            phase=phase,
            next_payload=next_payload,
            finalize_fn=finalize_baseline,
        )
    elif phase in {"baseline_complete", "iterating"}:
        payload = prepare_step(run_dir)
    elif phase == "step_in_progress":
        payload = _finalize_or_block(
            run_dir,
            phase=phase,
            next_payload=next_payload,
            finalize_fn=finalize_step,
        )
    elif phase == "paused":
        payload = build_resume_payload(run_dir, activate=activate_paused, limit=resume_limit)
    elif phase == "replan_required":
        payload = {
            "run_dir": str(run_dir),
            "phase": phase,
            "state_status": phase,
            "blocked": True,
            "next_action": next_payload["next_action"],
            "reason": "A user direction change or stale step requires replanning before execution can continue.",
            "required_reads": next_payload["required_reads"],
            "allowed_writes": next_payload["allowed_writes"],
            "success_condition": next_payload["success_condition"],
        }
    elif phase == "complete":
        payload = generate_report(run_dir)
    else:
        raise ValueError(f"Unsupported phase: {phase}")

    return {
        "run_dir": str(run_dir),
        "phase_before": phase,
        "advanced": payload.get("blocked") is not True,
        "result": payload,
    }


def _finalize_or_block(run_dir: Path, *, phase: str, next_payload: dict, finalize_fn) -> dict:
    try:
        return finalize_fn(run_dir)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "run_dir": str(run_dir),
            "phase": phase,
            "state_status": phase,
            "blocked": True,
            "next_action": next_payload["next_action"],
            "reason": str(exc),
            "required_reads": next_payload["required_reads"],
            "allowed_writes": next_payload["allowed_writes"],
            "success_condition": next_payload["success_condition"],
        }
