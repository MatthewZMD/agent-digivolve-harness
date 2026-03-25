from __future__ import annotations

from pathlib import Path

from .coordination import append_event, load_events, reset_active_step
from .readiness import assess_run_readiness
from .runtime import build_next_payload
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_spec, save_run_state


def configure_evaluators(
    run_dir: Path,
    *,
    mode: str,
    panel_size: int | None = None,
    subagent_system: str | None = None,
    external_agents: list[str] | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    _assert_prebaseline(state)

    evaluation = dict(spec.get("evaluation", {}))
    previous = {
        "mode": evaluation.get("evaluator_mode", "subagent"),
        "panel_size": max(1, int(evaluation.get("panel_size", 1))),
        "subagent_system": evaluation.get("subagent_system", "codex"),
        "external_agents": list(evaluation.get("external_agents", [])),
    }

    requested_panel_size = panel_size if panel_size is not None else previous["panel_size"]
    normalized_panel_size = max(1, int(requested_panel_size))
    normalized_external_agents = (
        _normalize_external_agents(external_agents) if external_agents is not None else None
    )

    if mode == "subagent":
        evaluation["evaluator_mode"] = "subagent"
        evaluation["subagent_system"] = (subagent_system or previous["subagent_system"] or "codex").strip()
        evaluation["panel_size"] = normalized_panel_size
        evaluation["external_agents"] = []
    elif mode == "external_panel":
        agents = normalized_external_agents
        if agents is None:
            existing = [item for item in previous["external_agents"] if item.strip()]
            agents = existing if existing else _default_external_agents(normalized_panel_size)
        if len(agents) < normalized_panel_size:
            raise ValueError(
                f"Need at least {normalized_panel_size} external evaluator ids, found {len(agents)}."
            )
        evaluation["evaluator_mode"] = "external_panel"
        evaluation["panel_size"] = normalized_panel_size
        evaluation["external_agents"] = agents
        if subagent_system:
            evaluation["subagent_system"] = subagent_system.strip()
    else:
        raise ValueError(f"Unsupported evaluator mode: {mode}")

    spec["evaluation"] = evaluation
    save_run_spec(run_dir, spec)

    readiness = assess_run_readiness(run_dir)
    if evaluation.get("require_confirmation", True):
        state["eval_confirmed"] = False
        state["status"] = "awaiting_confirmation" if readiness["ready_for_baseline"] else "draft"
    else:
        state["status"] = "ready" if readiness["ready_for_baseline"] else "draft"
    save_run_state(run_dir, state)

    reset_active_step(
        run_dir,
        reason="Evaluator strategy changed; refresh the current review packet.",
        source="configure-evaluators",
    )
    event = append_event(
        run_dir,
        event_type="evaluator_strategy_configured",
        summary=_event_summary(evaluation),
        details={
            "previous": previous,
            "current": {
                "mode": evaluation["evaluator_mode"],
                "panel_size": evaluation["panel_size"],
                "subagent_system": evaluation.get("subagent_system"),
                "external_agents": list(evaluation.get("external_agents", [])),
            },
            "confirmation_invalidated": True,
        },
    )
    next_payload = build_next_payload(run_dir)

    from .workpack import build_work_packet

    return {
        "run_dir": str(run_dir),
        "configured": True,
        "evaluation": {
            "mode": evaluation["evaluator_mode"],
            "panel_size": evaluation["panel_size"],
            "subagent_system": evaluation.get("subagent_system"),
            "external_agents": list(evaluation.get("external_agents", [])),
        },
        "state_status": state["status"],
        "eval_confirmed": state.get("eval_confirmed", False),
        "next_action": next_payload["next_action"],
        "event": event,
        "recent_events": load_events(run_dir, limit=5),
        "work_packet": build_work_packet(run_dir),
    }


def _assert_prebaseline(state: dict) -> None:
    if state.get("baseline_score") is not None:
        raise ValueError("Evaluator strategy can only be changed before baseline starts.")
    if int(state.get("current_experiment", 0) or 0) > 0:
        raise ValueError("Evaluator strategy can only be changed before baseline starts.")
    if state.get("status") in {"baseline_in_progress", "step_in_progress", "baseline_complete", "iterating", "complete"}:
        raise ValueError("Evaluator strategy can only be changed before baseline starts.")
    if state.get("paused_from") in {"baseline_in_progress", "step_in_progress", "baseline_complete", "iterating", "complete"}:
        raise ValueError("Evaluator strategy can only be changed before baseline starts.")


def _normalize_external_agents(external_agents: list[str] | None) -> list[str]:
    agents = [item.strip() for item in external_agents or [] if item and item.strip()]
    deduped: list[str] = []
    for item in agents:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _default_external_agents(panel_size: int) -> list[str]:
    return [f"external-agent-{index}" for index in range(1, max(1, int(panel_size)) + 1)]


def _event_summary(evaluation: dict) -> str:
    if evaluation.get("evaluator_mode") == "subagent":
        return (
            "Configured evaluator strategy to use the host system's built-in subagent "
            f"on `{evaluation.get('subagent_system', 'codex')}`."
        )
    return (
        "Configured evaluator strategy to use an external panel: "
        + ", ".join(list(evaluation.get("external_agents", [])))
    )
