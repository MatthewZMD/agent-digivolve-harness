from __future__ import annotations

from pathlib import Path

from .readiness import assess_run_readiness
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def confirm_evals(run_dir: Path, *, notes: str | None = None) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)

    if not readiness["ready_for_baseline"]:
        raise ValueError("Run is not ready for confirmation yet. Finish the eval package first.")
    if not state.get("eval_drafted"):
        raise ValueError("Run has not been drafted yet. Run `draft-evals` before confirmation.")

    state["eval_confirmed"] = True
    state["status"] = "ready"
    save_run_state(run_dir, state)

    confirmation_path = run_dir / "reports" / "eval_confirmation.md"
    confirmation_path.write_text(
        _confirmation_report(spec, notes=notes),
        encoding="utf-8",
    )

    return {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "state_status": state["status"],
        "eval_confirmed": True,
        "next_action": "run_baseline",
        "written_files": [
            str((run_dir / "state.json").resolve()),
            str(confirmation_path.resolve()),
        ],
    }


def _confirmation_report(spec: dict, *, notes: str | None) -> str:
    note_block = notes.strip() if notes and notes.strip() else "No extra confirmation notes recorded."
    return (
        "# Eval Confirmation\n\n"
        f"- run_id: `{spec['run_id']}`\n"
        f"- artifact_type: `{spec['artifact_type']}`\n"
        "- status: `confirmed`\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Evaluator Strategy\n\n"
        f"{_evaluator_summary(spec)}\n\n"
        "## Notes\n\n"
        f"{note_block}\n"
    )


def _evaluator_summary(spec: dict) -> str:
    evaluation = spec.get("evaluation", {})
    mode = evaluation.get("evaluator_mode", "subagent")
    panel_size = max(1, int(evaluation.get("panel_size", 1)))
    if mode == "subagent":
        return (
            f"- mode: built-in subagent\n"
            f"- host_system: `{evaluation.get('subagent_system', 'codex')}`\n"
            f"- subagent_model_policy: `{evaluation.get('subagent_model_policy', 'best_available')}`\n"
            f"- required_evaluators: `{panel_size}`\n"
            "- note: this uses the current host system's own subagent capability and should use the strongest available model on that host; today that is usually Codex, but the pattern can generalize to systems like Claude Code or OpenCode"
        )
    return (
        f"- mode: external panel\n"
        f"- required_evaluators: `{panel_size}`\n"
        f"- configured_slots: `{evaluation.get('external_agents', [])}`\n"
        "- note: this path lets the user choose an independent evaluator panel outside the current executor"
    )
