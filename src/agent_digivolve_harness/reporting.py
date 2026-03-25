from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from .coordination import load_active_step, load_events
from .journal import load_journal_entries
from .runtime import build_next_payload
from .workspace import load_run_spec, load_run_state, resolve_run_dir


def generate_report(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    experiments = _load_experiments(run_dir / "logs" / "experiments.tsv")

    keep_count = sum(1 for row in experiments if row["decision"] == "keep")
    discard_count = sum(1 for row in experiments if row["decision"] == "discard")
    baseline_rows = sum(1 for row in experiments if row["decision"] == "baseline")
    latest_rows = experiments[-5:]
    next_payload = build_next_payload(run_dir)
    journal_entries = load_journal_entries(run_dir / "logs" / "journal.jsonl")
    events = load_events(run_dir, limit=-1)
    active_step = load_active_step(run_dir)

    summary = {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "goal": spec["goal"],
        "artifact_type": spec["artifact_type"],
        "state_status": state.get("status", "draft"),
        "current_experiment": state.get("current_experiment", 0),
        "best_candidate": state.get("best_candidate", "baseline"),
        "baseline_score": state.get("baseline_score"),
        "best_score": state.get("best_score"),
        "score_delta": _score_delta(state.get("baseline_score"), state.get("best_score")),
        "experiments_total": len(experiments),
        "keep_count": keep_count,
        "discard_count": discard_count,
        "baseline_rows": baseline_rows,
        "journal_entries": len(journal_entries),
        "events_total": len(events),
        "replan_required": bool(state.get("replan_required")),
        "active_step_status": active_step.get("status"),
        "next_action": next_payload["next_action"],
        "phase": next_payload["phase"],
        "latest_experiments": latest_rows,
    }

    report_path = run_dir / "reports" / "run_report.md"
    report_path.write_text(_build_report_markdown(summary), encoding="utf-8")
    summary["report_path"] = str(report_path.resolve())
    return summary


def _load_experiments(path: Path) -> list[dict]:
    if not path.exists():
        return []
    contents = path.read_text(encoding="utf-8").strip()
    if not contents:
        return []
    reader = csv.DictReader(StringIO(contents), delimiter="\t")
    return list(reader)


def _score_delta(baseline_score: float | None, best_score: float | None) -> float | None:
    if baseline_score is None or best_score is None:
        return None
    return round(best_score - baseline_score, 2)


def _build_report_markdown(summary: dict) -> str:
    latest_lines = []
    for row in summary["latest_experiments"]:
        latest_lines.append(
            f"- exp {row['experiment']}: decision=`{row['decision']}`, pass_rate=`{row['pass_rate']}`, description={row['description']}"
        )
    if not latest_lines:
        latest_lines.append("- No experiments recorded yet.")

    delta = summary["score_delta"]
    delta_text = "n/a" if delta is None else f"{delta:+.2f}"

    return (
        "# Run Report\n\n"
        f"- run_id: `{summary['run_id']}`\n"
        f"- artifact_type: `{summary['artifact_type']}`\n"
        f"- status: `{summary['state_status']}`\n"
        f"- phase: `{summary['phase']}`\n"
        f"- next_action: `{summary['next_action']}`\n"
        f"- current_experiment: `{summary['current_experiment']}`\n"
        f"- best_candidate: `{summary['best_candidate']}`\n"
        f"- baseline_score: `{summary['baseline_score']}`\n"
        f"- best_score: `{summary['best_score']}`\n"
        f"- score_delta: `{delta_text}`\n"
        f"- experiments_total: `{summary['experiments_total']}`\n"
        f"- keep_count: `{summary['keep_count']}`\n"
        f"- discard_count: `{summary['discard_count']}`\n"
        f"- journal_entries: `{summary['journal_entries']}`\n"
        f"- events_total: `{summary['events_total']}`\n"
        f"- replan_required: `{summary['replan_required']}`\n"
        f"- active_step_status: `{summary['active_step_status']}`\n\n"
        "## Goal\n\n"
        f"{summary['goal']}\n\n"
        "## Recent Experiments\n\n"
        f"{'\n'.join(latest_lines)}\n"
    )
