from __future__ import annotations

from pathlib import Path

from .coordination import (
    active_step_path,
    append_event,
    events_path,
    load_active_step,
    load_events,
)
from .journal import load_journal_entries
from .readiness import assess_run_readiness
from .reporting import generate_report
from .runtime import build_next_payload, infer_operational_phase
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def build_resume_payload(run_dir: Path, *, activate: bool = False, limit: int = 3) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)
    report = generate_report(run_dir)
    next_payload = build_next_payload(run_dir)
    journal_entries = load_journal_entries(run_dir / "logs" / "journal.jsonl")
    recent_entries = journal_entries[-limit:] if limit >= 0 else journal_entries

    reactivate_to = None
    if state.get("status") == "paused":
        reactivate_to = (
            "replan_required"
            if state.get("replan_required")
            else state.get("paused_from") or infer_operational_phase(run_dir, spec, state, readiness)
        )
        if activate:
            state["status"] = reactivate_to
            if reactivate_to != "replan_required":
                state["paused_from"] = None
            save_run_state(run_dir, state)
            next_payload = build_next_payload(run_dir)
            append_event(
                run_dir,
                event_type="run_resumed",
                summary=f"Run reactivated to `{reactivate_to}`.",
                details={"reactivate_to": reactivate_to},
            )

    current_workspace = _current_workspace(run_dir, next_payload["phase"], state)
    prioritized_reads = _prioritized_reads(report["report_path"], next_payload, current_workspace)
    active_step = load_active_step(run_dir)
    recent_events = load_events(run_dir, limit=limit)

    payload = {
        "run_dir": str(run_dir),
        "state_status": state.get("status", "draft"),
        "phase": next_payload["phase"],
        "next_action": next_payload["next_action"],
        "report_path": report["report_path"],
        "prioritized_reads": prioritized_reads,
        "recent_journal_entries": recent_entries,
        "recent_events": recent_events,
        "active_step": active_step,
        "active_step_path": active_step_path(run_dir),
        "events_path": events_path(run_dir),
        "current_workspace": current_workspace,
        "reactivate_to": reactivate_to,
        "resumed": bool(activate and reactivate_to),
        "replan_required": bool(state.get("replan_required")),
        "replan_reason": state.get("replan_reason"),
        "agent_prompt": _agent_prompt(run_dir, next_payload["next_action"], next_payload["phase"], report["report_path"]),
    }
    return payload


def _current_workspace(run_dir: Path, phase: str, state: dict) -> dict | None:
    git_state = state.get("git", {})
    if phase == "baseline_in_progress":
        return {
            "type": "baseline",
            "output_dir": str((run_dir / "outputs" / "exp-000").resolve()),
            "score_dir": str((run_dir / "scores" / "exp-000").resolve()),
            "manifest_path": str((run_dir / "outputs" / "exp-000" / "manifest.json").resolve()),
            "runner_path": str((run_dir / "outputs" / "exp-000" / "runner.md").resolve()),
            "case_brief_dir": str((run_dir / "outputs" / "exp-000" / "cases").resolve()),
        }
    if phase == "step_in_progress":
        experiment_id = int(state.get("pending_experiment") or (state.get("current_experiment", 0) + 1))
        exp_name = f"exp-{experiment_id:03d}"
        return {
            "type": "step",
            "experiment_id": experiment_id,
            "worktree_path": git_state.get("active_worktree"),
            "output_dir": str((run_dir / "outputs" / exp_name).resolve()),
            "score_dir": str((run_dir / "scores" / exp_name).resolve()),
            "manifest_path": str((run_dir / "outputs" / exp_name / "manifest.json").resolve()),
            "runner_path": str((run_dir / "outputs" / exp_name / "runner.md").resolve()),
            "case_brief_dir": str((run_dir / "outputs" / exp_name / "cases").resolve()),
            "target_path": git_state.get("active_target_path"),
        }
    return None


def _prioritized_reads(report_path: str, next_payload: dict, current_workspace: dict | None) -> list[str]:
    reads = [report_path]
    run_dir = Path(report_path).resolve().parents[1]
    reads.append(active_step_path(run_dir))
    reads.append(events_path(run_dir))
    if current_workspace:
        runner_path = current_workspace.get("runner_path")
        if runner_path:
            reads.append(runner_path)
        manifest_path = current_workspace.get("manifest_path")
        if manifest_path:
            reads.append(manifest_path)
        target_path = current_workspace.get("target_path")
        if target_path:
            reads.append(target_path)
        worktree_path = current_workspace.get("worktree_path")
        if worktree_path:
            reads.append(worktree_path)
    for item in next_payload["required_reads"]:
        if item not in reads:
            reads.append(item)
    return reads


def _agent_prompt(run_dir: Path, next_action: str, phase: str, report_path: str) -> str:
    if phase == "replan_required":
        return (
            f"Continue the run in {run_dir}. "
            f"Read {report_path}, the active step snapshot, and the event log first, "
            f"then reconcile the direction change before resuming execution."
        )
    return (
        f"Continue the run in {run_dir}. "
        f"Read {report_path}, the active step snapshot, and the event log first, "
        f"then follow the current phase `{phase}` and execute `{next_action}`."
    )
