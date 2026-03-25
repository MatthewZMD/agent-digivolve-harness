from __future__ import annotations

from pathlib import Path

from .coordination import (
    append_event,
    load_active_step,
    load_events,
    reset_active_step,
    save_active_step,
)
from .readiness import assess_run_readiness
from .runtime import build_next_payload, infer_operational_phase
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def add_run_note(run_dir: Path, message: str) -> dict:
    run_dir = resolve_run_dir(run_dir)
    event = append_event(
        run_dir,
        event_type="user_note",
        summary=message,
        details={"phase": _operational_phase(run_dir)},
    )
    return {
        "run_dir": str(run_dir),
        "recorded": True,
        "event": event,
        "recent_events": load_events(run_dir, limit=5),
    }


def interrupt_run(run_dir: Path, reason: str) -> dict:
    run_dir = resolve_run_dir(run_dir)
    state = load_run_state(run_dir)
    current_phase = _operational_phase(run_dir)
    paused_from = state.get("paused_from") or current_phase

    active_step = load_active_step(run_dir)
    active_step.update(
        {
            "status": "interrupted",
            "phase": active_step.get("phase") or current_phase,
            "reason": reason.strip(),
            "source": "interrupt",
        }
    )
    active_step = save_active_step(run_dir, active_step)

    state["status"] = "paused"
    state["paused_from"] = paused_from
    save_run_state(run_dir, state)

    event = append_event(
        run_dir,
        event_type="user_interrupt",
        summary=reason,
        details={"paused_from": paused_from},
    )
    return {
        "run_dir": str(run_dir),
        "state_status": "paused",
        "paused_from": paused_from,
        "next_action": "resume",
        "active_step": active_step,
        "event": event,
    }


def change_run_direction(run_dir: Path, request: str) -> dict:
    run_dir = resolve_run_dir(run_dir)
    state = load_run_state(run_dir)
    current_phase = _operational_phase(run_dir)
    paused_from = state.get("paused_from") or current_phase

    active_step = load_active_step(run_dir)
    active_step.update(
        {
            "status": "stale",
            "phase": active_step.get("phase") or current_phase,
            "reason": request.strip(),
            "source": "change-direction",
        }
    )
    active_step = save_active_step(run_dir, active_step)

    state["status"] = "paused"
    state["paused_from"] = paused_from
    state["replan_required"] = True
    state["replan_reason"] = request.strip()
    save_run_state(run_dir, state)

    event = append_event(
        run_dir,
        event_type="direction_change_requested",
        summary=request,
        details={"paused_from": paused_from},
    )
    return {
        "run_dir": str(run_dir),
        "state_status": "paused",
        "paused_from": paused_from,
        "replan_required": True,
        "next_action": "replan",
        "active_step": active_step,
        "event": event,
    }


def resolve_replan(run_dir: Path, *, summary: str, next_step: str | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    state = load_run_state(run_dir)
    spec = load_run_spec(run_dir)
    readiness = assess_run_readiness(run_dir)

    state["replan_required"] = False
    state["replan_reason"] = None
    target_phase = infer_operational_phase(run_dir, spec, state, readiness)
    state["status"] = target_phase
    state["paused_from"] = None
    save_run_state(run_dir, state)
    reset_active_step(run_dir, reason="Replan recorded; refresh the current work packet.", source="replan")

    event = append_event(
        run_dir,
        event_type="replan_recorded",
        summary=summary,
        details={
            "next_step": next_step,
            "reactivated_to": target_phase,
        },
    )
    next_payload = build_next_payload(run_dir)

    payload = {
        "run_dir": str(run_dir),
        "recorded": True,
        "state_status": state["status"],
        "phase": next_payload["phase"],
        "next_action": next_payload["next_action"],
        "event": event,
        "recent_events": load_events(run_dir, limit=5),
    }

    from .workpack import build_work_packet

    payload["work_packet"] = build_work_packet(run_dir)
    return payload


def _operational_phase(run_dir: Path) -> str:
    state = load_run_state(run_dir)
    if state.get("status") == "paused":
        return state.get("paused_from") or "paused"
    if state.get("status") == "replan_required":
        return "replan_required"
    spec = load_run_spec(run_dir)
    readiness = assess_run_readiness(run_dir)
    return infer_operational_phase(run_dir, spec, state, readiness)
