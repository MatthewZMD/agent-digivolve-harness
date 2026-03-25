from __future__ import annotations

from pathlib import Path

from .advance import advance_run
from .experiments import experiment_status
from .runtime import build_next_payload
from .workpack import build_work_packet
from .workspace import resolve_run_dir


STOP_PHASES = {"draft", "awaiting_confirmation", "baseline_in_progress", "step_in_progress", "paused", "replan_required", "complete"}


def run_loop(
    run_dir: Path,
    *,
    max_transitions: int = 10,
    activate_paused: bool = False,
    resume_limit: int = 3,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    trace: list[dict] = []

    if max_transitions <= 0:
        raise ValueError("max_transitions must be positive.")

    for _ in range(max_transitions):
        before = build_next_payload(run_dir)
        step = advance_run(
            run_dir,
            activate_paused=activate_paused,
            resume_limit=resume_limit,
        )
        after = build_next_payload(run_dir)

        trace_item = {
            "phase_before": before["phase"],
            "phase_after": after["phase"],
            "next_action_before": before["next_action"],
            "advanced": step["advanced"],
            "result": step["result"],
        }
        trace.append(trace_item)

        stop_reason = _stop_reason(before, after, step)
        if stop_reason is not None:
            payload = {
                "run_dir": str(run_dir),
                "transitions": len(trace),
                "stopped": True,
                "stop_reason": stop_reason,
                "phase": after["phase"],
                "next_action": after["next_action"],
                "trace": trace,
            }
            experiment = _maybe_experiment_status(run_dir, after["phase"])
            if experiment is not None:
                payload["experiment"] = experiment
            if stop_reason in {
                "draft_work_required",
                "confirmation_required",
                "experiment_work_required",
                "blocked",
                "paused",
                "replan_required",
                "complete",
            }:
                payload["work_packet"] = build_work_packet(run_dir)
            return payload

    final = build_next_payload(run_dir)
    payload = {
        "run_dir": str(run_dir),
        "transitions": len(trace),
        "stopped": False,
        "stop_reason": "max_transitions_reached",
        "phase": final["phase"],
        "next_action": final["next_action"],
        "trace": trace,
    }
    experiment = _maybe_experiment_status(run_dir, final["phase"])
    if experiment is not None:
        payload["experiment"] = experiment
    return payload


def _stop_reason(before: dict, after: dict, step: dict) -> str | None:
    if after["phase"] == "awaiting_confirmation":
        return "confirmation_required"
    if not step["advanced"]:
        return "blocked"
    if after["phase"] in {"baseline_in_progress", "step_in_progress"}:
        return "experiment_work_required"
    if after["phase"] == "draft":
        return "draft_work_required"
    if after["phase"] == "paused":
        return "paused"
    if after["phase"] == "replan_required":
        return "replan_required"
    if after["phase"] == "complete":
        return "complete"
    if after["phase"] == before["phase"] and after["next_action"] == before["next_action"]:
        return "no_further_automatic_progress"
    return None


def _maybe_experiment_status(run_dir: Path, phase: str) -> dict | None:
    if phase not in {"baseline_in_progress", "step_in_progress"}:
        return None
    return experiment_status(run_dir)
