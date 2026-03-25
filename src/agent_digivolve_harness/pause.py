from __future__ import annotations

from pathlib import Path

from .coordination import append_event, load_active_step, save_active_step
from .readiness import assess_run_readiness
from .workspace import load_run_spec, resolve_run_dir
from .runtime import infer_operational_phase
from .workspace import load_run_state, save_run_state


def pause_run(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)
    current_phase = infer_operational_phase(run_dir, spec, state, readiness)

    state["paused_from"] = current_phase
    state["status"] = "paused"
    save_run_state(run_dir, state)
    active_step = load_active_step(run_dir)
    if active_step.get("status") != "idle":
        active_step["status"] = "paused"
        active_step["phase"] = active_step.get("phase") or current_phase
        active_step["source"] = "pause"
        active_step["reason"] = f"Run paused from `{current_phase}`."
        save_active_step(run_dir, active_step)
    event = append_event(
        run_dir,
        event_type="run_paused",
        summary=f"Run paused from `{current_phase}`.",
        details={"paused_from": current_phase},
    )

    return {
        "run_dir": str(run_dir),
        "state_status": "paused",
        "paused_from": current_phase,
        "next_action": "resume",
        "event": event,
        "written_files": [str((run_dir / "state.json").resolve())],
    }
