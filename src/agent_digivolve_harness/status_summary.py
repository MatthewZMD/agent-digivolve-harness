from __future__ import annotations

from pathlib import Path

from .coordination import load_active_step, load_events
from .experiments import experiment_status
from .readiness import assess_run_readiness
from .runtime import build_next_payload
from .workspace import load_run_spec, load_run_state, resolve_run_dir


def build_status_summary(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    next_payload = build_next_payload(run_dir)
    display_phase = _display_phase(state, next_payload["phase"])
    readiness = assess_run_readiness(run_dir)
    active_step = load_active_step(run_dir)
    recent_events = load_events(run_dir, limit=10)
    latest_user_change = _latest_user_change(recent_events)

    payload = {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "goal": spec["goal"],
        "artifact_type": spec["artifact_type"],
        "phase": display_phase,
        "harness_phase": next_payload["phase"],
        "harness_next_action": next_payload["next_action"],
        "active_step_status": active_step.get("status"),
        "latest_user_change": latest_user_change,
        "waiting_for_user": False,
        "waiting_reason": None,
        "completed_milestones": _completed_milestones(state, readiness),
        "current_step": active_step.get("summary"),
        "progress_summary": None,
        "next_for_codex": None,
        "user_update": None,
    }

    payload.update(_phase_summary(run_dir, display_phase, state, readiness, active_step))
    return payload


def _phase_summary(
    run_dir: Path,
    phase: str,
    state: dict,
    readiness: dict,
    active_step: dict,
) -> dict:
    if phase == "draft":
        completed, remaining = _readiness_lists(readiness)
        progress_summary = f"{len(completed)}/5 setup items are ready."
        user_update = (
            "The run is still being set up. "
            f"{progress_summary} "
            + (_list_sentence("Completed", completed) + " " if completed else "")
            + (_list_sentence("Still needed", remaining) if remaining else "")
        ).strip()
        return {
            "headline": "Run Setup In Progress",
            "current_step": active_step.get("summary") or "Define the artifact and evaluation package.",
            "progress_summary": progress_summary,
            "next_for_codex": "Finish the missing artifact, checks, judge prompt, or cases, then rerun the draft flow.",
            "user_update": user_update,
        }

    if phase == "awaiting_confirmation":
        return {
            "headline": "Waiting For Approval Before Baseline",
            "current_step": active_step.get("summary") or "Review the drafted eval package with the user.",
            "progress_summary": "The artifact, checks, judge prompt, and cases are ready.",
            "waiting_for_user": True,
            "waiting_reason": "Baseline cannot start until the user explicitly approves the eval package and evaluator strategy.",
            "next_for_codex": "Walk the user through the eval package and evaluator strategy, revise them if requested, and only then confirm baseline readiness.",
            "user_update": "The eval package is drafted and ready for review. The run is waiting for your approval on both the evaluation package and the evaluator strategy before baseline can start.",
        }

    if phase == "ready":
        return {
            "headline": "Ready To Start Baseline",
            "current_step": "Materialize and run baseline experiment #0.",
            "progress_summary": "The eval package is confirmed and the run is ready for baseline.",
            "next_for_codex": "Start the baseline experiment.",
            "user_update": "Setup is complete and baseline can start now.",
        }

    if phase in {"baseline_in_progress", "step_in_progress"}:
        status = experiment_status(run_dir)
        experiment_label = "baseline" if status["kind"] == "baseline" else f"iteration {status['experiment_id']}"
        if status["finalizable"]:
            progress_summary = f"All {status['total_cases']} {experiment_label} cases are recorded and the summary is ready."
        else:
            progress_summary = (
                f"{status['ready_cases']}/{status['total_cases']} {experiment_label} cases are recorded. "
                f"Summary ready: {status['summary_ready']}."
            )
        user_update = (
            f"The run is executing {experiment_label}. "
            f"{progress_summary}"
        )
        return {
            "headline": "Experiment In Progress",
            "current_step": active_step.get("summary") or f"Complete the active {status['kind']} experiment.",
            "progress_summary": progress_summary,
            "next_for_codex": "Finish the remaining case work, write the summary, and finalize the experiment when all required files are ready.",
            "user_update": user_update,
            "experiment_progress": {
                "kind": status["kind"],
                "experiment_id": status["experiment_id"],
                "ready_cases": status["ready_cases"],
                "total_cases": status["total_cases"],
                "summary_ready": status["summary_ready"],
            },
        }

    if phase == "baseline_complete":
        score_summary = _score_summary(state)
        return {
            "headline": "Baseline Complete",
            "current_step": "Prepare the first candidate iteration.",
            "progress_summary": score_summary,
            "next_for_codex": "Analyze the baseline and start the first bounded iteration.",
            "user_update": f"Baseline is complete. {score_summary} The next step is to propose and evaluate the first improvement.",
        }

    if phase == "iterating":
        score_summary = _score_summary(state)
        return {
            "headline": "Ready For Next Iteration",
            "current_step": "Prepare the next candidate experiment.",
            "progress_summary": score_summary,
            "next_for_codex": "Start the next bounded iteration from the current best candidate.",
            "user_update": f"At least one iteration has completed. {score_summary} Codex can now start the next improvement round.",
        }

    if phase == "paused":
        current = active_step.get("summary") or _phase_label(state.get("paused_from"))
        return {
            "headline": "Run Paused",
            "current_step": current,
            "progress_summary": f"The run is paused from `{state.get('paused_from') or 'unknown'}`.",
            "next_for_codex": "Resume the run, reread the recovery payload, and continue from the saved step snapshot.",
            "user_update": f"The run is paused. The last active work was: {current}",
        }

    if phase == "replan_required":
        reason = state.get("replan_reason") or "The current direction changed."
        return {
            "headline": "Replanning Required",
            "current_step": "Reconcile the new direction before more execution happens.",
            "progress_summary": reason,
            "next_for_codex": "Update the run artifacts to reflect the new direction, then record the replan and continue.",
            "user_update": "Your updated direction has been recorded. Codex now needs to replan before continuing.",
        }

    if phase == "complete":
        score_summary = _score_summary(state)
        return {
            "headline": "Run Complete",
            "current_step": "Summarize and present the final outcome.",
            "progress_summary": score_summary,
            "next_for_codex": "Present the final outcome and any relevant report details.",
            "user_update": f"The run is complete. {score_summary}",
        }

    return {
        "headline": "Run Active",
        "current_step": active_step.get("summary") or "Continue the run.",
        "progress_summary": f"The run is currently in phase `{phase}`.",
        "next_for_codex": "Read the current work packet and continue.",
        "user_update": f"The run is in phase `{phase}`.",
    }


def _completed_milestones(state: dict, readiness: dict) -> list[str]:
    milestones = ["Run initialized"]
    if readiness["artifact"]["ready"]:
        milestones.append("Target ready")
    if readiness["checks"]["ready"]:
        milestones.append("Checks ready")
    if readiness["judge"]["ready"]:
        milestones.append("Judge prompt ready")
    if readiness["train_cases"]["ready"] and readiness["holdout_cases"]["ready"]:
        milestones.append("Cases ready")
    if state.get("eval_confirmed"):
        milestones.append("Eval package confirmed")
    if state.get("baseline_score") is not None:
        milestones.append("Baseline complete")
    if state.get("current_experiment", 0) > 0:
        milestones.append("At least one iteration completed")
    return milestones


def _readiness_lists(readiness: dict) -> tuple[list[str], list[str]]:
    items = [
        ("Target", readiness["artifact"]["ready"]),
        ("Checks", readiness["checks"]["ready"]),
        ("Judge prompt", readiness["judge"]["ready"]),
        ("Train cases", readiness["train_cases"]["ready"]),
        ("Holdout cases", readiness["holdout_cases"]["ready"]),
    ]
    completed = [label for label, ready in items if ready]
    remaining = [label for label, ready in items if not ready]
    return completed, remaining


def _list_sentence(prefix: str, items: list[str]) -> str:
    return f"{prefix}: {', '.join(items)}."


def _score_summary(state: dict) -> str:
    baseline = state.get("baseline_score")
    best = state.get("best_score")
    if baseline is None and best is None:
        return "No scored experiment is recorded yet."
    if baseline is not None and best is None:
        return f"Baseline score: {baseline}."
    if baseline is None:
        return f"Best score: {best}."
    return f"Baseline score: {baseline}. Best score so far: {best}."


def _phase_label(phase: str | None) -> str:
    if not phase:
        return "the previous saved step"
    return f"the `{phase}` step"


def _display_phase(state: dict, harness_phase: str) -> str:
    if state.get("replan_required"):
        return "replan_required"
    return harness_phase


def _latest_user_change(events: list[dict]) -> dict | None:
    interesting = {"user_note", "user_interrupt", "direction_change_requested"}
    for event in reversed(events):
        if event.get("event_type") in interesting:
            return event
    return None
