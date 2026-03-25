from __future__ import annotations

import argparse
import json
from pathlib import Path

from .advance import advance_run
from .baseline import finalize_baseline, prepare_baseline
from .confirmation import confirm_evals
from .drafting import draft_evals
from .evaluator_strategy import configure_evaluators
from .execution import (
    list_cases,
    list_case_evaluations,
    load_case,
    load_runner,
    record_case,
    record_eval,
    record_summary,
    finalize_case,
    validate_case,
)
from .experiments import complete_experiment, experiment_status
from .coordination import load_events
from .interventions import add_run_note, change_run_direction, interrupt_run, resolve_replan
from .journal import load_journal_entries
from .loop import run_loop
from .models import InitOptions
from .pause import pause_run
from .openrouter_eval import openrouter_panel_eval
from .reporting import generate_report
from .resume import build_resume_payload
from .runtime import build_next_payload
from .scaffold import create_run_scaffold
from .status_summary import build_status_summary
from .step import finalize_step, prepare_step
from .workspace import resolve_run_dir
from .workpack import build_work_packet


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digivolve",
        description="Harness for evaluation-driven optimization runs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Initialize a new run scaffold."
    )
    init_parser.add_argument(
        "run_dir",
        help="Run reference for the new run. The scaffold will be materialized under the system temp run root.",
    )
    init_parser.add_argument("--goal", required=True, help="Natural-language goal.")
    init_parser.add_argument(
        "--artifact-type",
        required=True,
        choices=["prompt", "document-copy", "repo-task"],
        help="Type of artifact to optimize.",
    )
    init_parser.add_argument(
        "--artifact-path",
        help="Local target file or directory to optimize. If omitted, the harness bootstraps a target outside the run directory.",
    )
    init_parser.add_argument("--run-id", help="Optional explicit run identifier.")
    init_parser.add_argument(
        "--evaluation-mode",
        choices=["subagent", "external_panel"],
        default="subagent",
        help="How official evaluation must be produced for each case.",
    )
    init_parser.add_argument(
        "--subagent-system",
        default="codex",
        help="When using subagent evaluation, which host system owns the built-in subagent capability.",
    )
    init_parser.add_argument(
        "--panel-size",
        type=int,
        default=1,
        help="Required number of independent evaluator verdicts per case.",
    )
    init_parser.add_argument(
        "--max-experiments", type=int, default=20, help="Maximum experiment count."
    )
    init_parser.add_argument(
        "--max-judge-calls", type=int, default=200, help="Maximum judge calls."
    )
    init_parser.add_argument(
        "--max-subagents", type=int, default=2, help="Maximum subagent count."
    )
    init_parser.add_argument(
        "--max-wall-clock-minutes",
        type=int,
        default=60,
        help="Maximum wall clock time in minutes.",
    )

    next_parser = subparsers.add_parser(
        "next", help="Emit the machine-readable next step for a run."
    )
    next_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    work_packet_parser = subparsers.add_parser(
        "work-packet",
        help="Build a structured agent work packet for the current run state.",
    )
    work_packet_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    advance_parser = subparsers.add_parser(
        "advance",
        help="Advance the run by one legal lifecycle step based on the current phase.",
    )
    advance_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    advance_parser.add_argument(
        "--activate",
        action="store_true",
        help="If the run is paused, reactivate it while building the resume payload.",
    )
    advance_parser.add_argument(
        "--resume-limit",
        type=int,
        default=3,
        help="Maximum number of recent journal entries to include when resuming.",
    )

    loop_parser = subparsers.add_parser(
        "run-loop",
        help="Advance deterministic lifecycle steps until semantic work is required or the loop hits a cap.",
    )
    loop_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    loop_parser.add_argument(
        "--max-transitions",
        type=int,
        default=10,
        help="Maximum number of automatic transitions to perform.",
    )
    loop_parser.add_argument(
        "--activate",
        action="store_true",
        help="If the run is paused, reactivate it before continuing the loop.",
    )
    loop_parser.add_argument(
        "--resume-limit",
        type=int,
        default=3,
        help="Maximum number of recent journal entries to include when resuming.",
    )

    draft_parser = subparsers.add_parser(
        "draft-evals", help="Assess eval readiness and write an eval drafting report."
    )
    draft_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    confirm_parser = subparsers.add_parser(
        "confirm-evals",
        help="Explicitly confirm the drafted eval package so baseline can start.",
    )
    confirm_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    confirm_parser.add_argument(
        "--notes",
        help="Optional confirmation notes from the user or operator.",
    )

    configure_evaluators_parser = subparsers.add_parser(
        "configure-evaluators",
        help="Update the evaluator strategy chosen with the user and refresh confirmation state.",
    )
    configure_evaluators_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    configure_evaluators_parser.add_argument(
        "--mode",
        required=True,
        choices=["subagent", "external_panel"],
        help="Which independent evaluator path should be used for official scoring.",
    )
    configure_evaluators_parser.add_argument(
        "--panel-size",
        type=int,
        help="Required number of independent evaluator verdicts per case.",
    )
    configure_evaluators_parser.add_argument(
        "--subagent-system",
        help="When using subagent evaluation, which host system owns the built-in subagent capability.",
    )
    configure_evaluators_parser.add_argument(
        "--external-agent",
        action="append",
        default=None,
        help="External evaluator identifier or model id. Repeatable.",
    )

    baseline_parser = subparsers.add_parser(
        "baseline", help="Prepare baseline execution artifacts for experiment #0."
    )
    baseline_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    finalize_baseline_parser = subparsers.add_parser(
        "finalize-baseline",
        help="Validate baseline results and advance the run to baseline_complete.",
    )
    finalize_baseline_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    step_parser = subparsers.add_parser(
        "step", help="Prepare the next experiment candidate workspace."
    )
    step_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    finalize_step_parser = subparsers.add_parser(
        "finalize-step",
        help="Validate a prepared candidate and apply keep/discard to the run.",
    )
    finalize_step_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    report_parser = subparsers.add_parser(
        "report", help="Write and print a run summary report."
    )
    report_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    status_parser = subparsers.add_parser(
        "status", help="Print a user-facing progress summary for a run."
    )
    status_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    experiment_status_parser = subparsers.add_parser(
        "experiment-status",
        help="Report readiness and missing pieces for the active or selected experiment.",
    )
    experiment_status_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    experiment_status_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )

    complete_experiment_parser = subparsers.add_parser(
        "complete-experiment",
        help="Finalize the active or selected experiment when all case files and summary are ready.",
    )
    complete_experiment_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    complete_experiment_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )

    runner_parser = subparsers.add_parser(
        "runner", help="Print the runner payload for the active or selected experiment."
    )
    runner_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    runner_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )

    cases_parser = subparsers.add_parser(
        "cases", help="List case contracts for the active or selected experiment."
    )
    cases_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    cases_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )

    case_parser = subparsers.add_parser(
        "case", help="Print a single case bundle for the active or selected experiment."
    )
    case_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    case_parser.add_argument("case_id", help="Case identifier.")
    case_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    case_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )

    case_evals_parser = subparsers.add_parser(
        "case-evals", help="List independent evaluator verdicts recorded for one case."
    )
    case_evals_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    case_evals_parser.add_argument("case_id", help="Case identifier.")
    case_evals_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    case_evals_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )

    validate_case_parser = subparsers.add_parser(
        "validate-case", help="Validate one case output + score pair."
    )
    validate_case_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    validate_case_parser.add_argument("case_id", help="Case identifier.")
    validate_case_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    validate_case_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )

    record_case_parser = subparsers.add_parser(
        "record-case", help="Legacy direct score writer for runs that do not require independent evaluators."
    )
    record_case_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    record_case_parser.add_argument("case_id", help="Case identifier.")
    record_case_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    record_case_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )
    output_group = record_case_parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument(
        "--output-text",
        help="Raw output text to write for the case.",
    )
    output_group.add_argument(
        "--output-file",
        help="Path to a file whose contents should be recorded as the raw output.",
    )
    record_case_parser.add_argument(
        "--score",
        type=float,
        required=True,
        help="Numeric case score.",
    )
    record_case_parser.add_argument(
        "--notes",
        required=True,
        help="Concise scoring notes.",
    )
    record_case_parser.add_argument(
        "--passed-check",
        action="append",
        default=[],
        help="Check id marked as passed. Repeatable.",
    )
    record_case_parser.add_argument(
        "--failed-check",
        action="append",
        default=[],
        help="Check id marked as failed. Repeatable.",
    )

    record_eval_parser = subparsers.add_parser(
        "record-eval",
        help="Record one independent evaluator verdict for a case.",
    )
    record_eval_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    record_eval_parser.add_argument("case_id", help="Case identifier.")
    record_eval_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    record_eval_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )
    record_eval_parser.add_argument(
        "--check-id",
        required=True,
        help="Which single check this isolated evaluator verdict is for.",
    )
    record_eval_parser.add_argument("--evaluator-id", required=True, help="Stable evaluator identifier.")
    record_eval_parser.add_argument(
        "--evaluator-kind",
        required=True,
        choices=["subagent", "openrouter", "external_agent"],
        help="How this independent evaluator was run.",
    )
    record_eval_parser.add_argument(
        "--evaluator-label",
        help="Optional human-readable label for the evaluator.",
    )
    record_eval_parser.add_argument(
        "--model-name",
        help="Optional model name used by the evaluator.",
    )
    verdict_group = record_eval_parser.add_mutually_exclusive_group(required=True)
    verdict_group.add_argument(
        "--passed",
        action="store_true",
        help="Mark this isolated check verdict as passed.",
    )
    verdict_group.add_argument(
        "--failed",
        action="store_true",
        help="Mark this isolated check verdict as failed.",
    )
    record_eval_parser.add_argument(
        "--notes",
        required=True,
        help="Concise evaluator notes.",
    )

    finalize_case_parser = subparsers.add_parser(
        "finalize-case",
        help="Aggregate recorded evaluator verdicts into the official case score JSON.",
    )
    finalize_case_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    finalize_case_parser.add_argument("case_id", help="Case identifier.")
    finalize_case_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    finalize_case_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )

    openrouter_panel_eval_parser = subparsers.add_parser(
        "openrouter-panel-eval",
        help="Run one case through the configured OpenRouter external evaluator panel and record verdicts.",
    )
    openrouter_panel_eval_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    openrouter_panel_eval_parser.add_argument("case_id", help="Case identifier.")
    openrouter_panel_eval_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    openrouter_panel_eval_parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        help="Optional split when a case id is ambiguous.",
    )
    openrouter_panel_eval_parser.add_argument(
        "--model",
        action="append",
        default=None,
        help="Override configured OpenRouter model ids. Repeatable.",
    )
    openrouter_panel_eval_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="Network timeout for each OpenRouter request.",
    )

    record_summary_parser = subparsers.add_parser(
        "record-summary",
        help="Write the experiment summary JSON from completed case results.",
    )
    record_summary_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    record_summary_parser.add_argument(
        "--experiment-id",
        type=int,
        help="Optional experiment id. Defaults to the active experiment.",
    )
    record_summary_parser.add_argument(
        "--summary",
        required=True,
        help="Concise experiment summary.",
    )
    record_summary_parser.add_argument(
        "--mutation-description",
        help="Required for step summaries.",
    )

    journal_parser = subparsers.add_parser(
        "journal", help="Print structured journal entries for a run."
    )
    journal_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    journal_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of most recent entries to print.",
    )

    events_parser = subparsers.add_parser(
        "events", help="Print structured operational event entries for a run."
    )
    events_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    events_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of most recent event entries to print.",
    )

    pause_parser = subparsers.add_parser(
        "pause", help="Pause a run and record the current operational phase."
    )
    pause_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")

    interrupt_parser = subparsers.add_parser(
        "interrupt",
        help="Interrupt the current step, preserve the snapshot, and pause the run.",
    )
    interrupt_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    interrupt_parser.add_argument(
        "--reason",
        required=True,
        help="Why the current step was interrupted or cancelled.",
    )

    note_parser = subparsers.add_parser(
        "note",
        help="Record a user or operator note in the run event log without changing phase.",
    )
    note_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    note_parser.add_argument(
        "--message",
        required=True,
        help="Free-form note to add to the run event log.",
    )

    change_direction_parser = subparsers.add_parser(
        "change-direction",
        help="Mark the current work as stale, pause the run, and require replanning.",
    )
    change_direction_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    change_direction_parser.add_argument(
        "--request",
        required=True,
        help="What changed in the user's direction or success criteria.",
    )

    replan_parser = subparsers.add_parser(
        "replan",
        help="Record a reconciled direction after an interruption or user change request.",
    )
    replan_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    replan_parser.add_argument(
        "--summary",
        required=True,
        help="Summary of how the new direction was reconciled.",
    )
    replan_parser.add_argument(
        "--next-step",
        help="Optional next step to store alongside the replan event.",
    )

    resume_parser = subparsers.add_parser(
        "resume", help="Build a recovery payload for resuming a run."
    )
    resume_parser.add_argument("run_dir", help="Run reference or canonical tmp-backed run path.")
    resume_parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Maximum number of recent journal entries to include.",
    )
    resume_parser.add_argument(
        "--activate",
        action="store_true",
        help="If the run is paused, reactivate it to the inferred operational phase.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    run_dir = resolve_run_dir(Path(args.run_dir)) if hasattr(args, "run_dir") else None

    if args.command == "init":
        options = InitOptions(
            goal=args.goal,
            artifact_type=args.artifact_type,
            artifact_path=args.artifact_path,
            run_id=args.run_id,
            evaluator_mode=args.evaluation_mode,
            subagent_system=args.subagent_system,
            panel_size=args.panel_size,
            max_experiments=args.max_experiments,
            max_judge_calls=args.max_judge_calls,
            max_subagents=args.max_subagents,
            max_wall_clock_minutes=args.max_wall_clock_minutes,
        )
        spec = create_run_scaffold(run_dir, options)
        print(f"Initialized run scaffold at {run_dir}")
        print(f"run_id: {spec.run_id}")
        print(f"artifact_type: {spec.artifact_type}")
        print("next: fill in cases/, refine evals/, then run baseline.")
        return 0
    if args.command == "next":
        payload = build_next_payload(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "work-packet":
        payload = build_work_packet(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "advance":
        payload = advance_run(
            run_dir,
            activate_paused=args.activate,
            resume_limit=args.resume_limit,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "run-loop":
        payload = run_loop(
            run_dir,
            max_transitions=args.max_transitions,
            activate_paused=args.activate,
            resume_limit=args.resume_limit,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "draft-evals":
        payload = draft_evals(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "confirm-evals":
        payload = confirm_evals(run_dir, notes=args.notes)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "configure-evaluators":
        payload = configure_evaluators(
            run_dir,
            mode=args.mode,
            panel_size=args.panel_size,
            subagent_system=args.subagent_system,
            external_agents=args.external_agent,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "baseline":
        payload = prepare_baseline(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "finalize-baseline":
        payload = finalize_baseline(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "step":
        payload = prepare_step(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "finalize-step":
        payload = finalize_step(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "report":
        payload = generate_report(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "status":
        payload = build_status_summary(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "experiment-status":
        payload = experiment_status(run_dir, experiment_id=args.experiment_id)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "complete-experiment":
        payload = complete_experiment(run_dir, experiment_id=args.experiment_id)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "runner":
        payload = load_runner(run_dir, experiment_id=args.experiment_id)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "cases":
        payload = list_cases(run_dir, experiment_id=args.experiment_id)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "case":
        payload = load_case(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "case-evals":
        payload = list_case_evaluations(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "validate-case":
        payload = validate_case(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "record-case":
        payload = record_case(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
            output_text=args.output_text,
            output_file=args.output_file,
            score=args.score,
            notes=args.notes,
            passed_checks=args.passed_check,
            failed_checks=args.failed_check,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "record-eval":
        payload = record_eval(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
            evaluator_id=args.evaluator_id,
            evaluator_kind=args.evaluator_kind,
            evaluator_label=args.evaluator_label,
            model_name=args.model_name,
            check_id=args.check_id,
            passed=bool(args.passed),
            notes=args.notes,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "finalize-case":
        payload = finalize_case(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "openrouter-panel-eval":
        payload = openrouter_panel_eval(
            run_dir,
            args.case_id,
            experiment_id=args.experiment_id,
            split=args.split,
            models=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "record-summary":
        payload = record_summary(
            run_dir,
            experiment_id=args.experiment_id,
            summary=args.summary,
            mutation_description=args.mutation_description,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "journal":
        entries = load_journal_entries(run_dir / "logs" / "journal.jsonl")
        if args.limit >= 0:
            entries = entries[-args.limit :]
        payload = {
            "run_dir": str(run_dir),
            "entries": entries,
            "count": len(entries),
        }
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "events":
        entries = load_events(run_dir, limit=args.limit)
        payload = {
            "run_dir": str(run_dir),
            "entries": entries,
            "count": len(entries),
        }
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "pause":
        payload = pause_run(run_dir)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "interrupt":
        payload = interrupt_run(run_dir, args.reason)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "note":
        payload = add_run_note(run_dir, args.message)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "change-direction":
        payload = change_run_direction(run_dir, args.request)
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "replan":
        payload = resolve_replan(
            run_dir,
            summary=args.summary,
            next_step=args.next_step,
        )
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "resume":
        payload = build_resume_payload(
            run_dir,
            activate=args.activate,
            limit=args.limit,
        )
        print(json.dumps(payload, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
