from __future__ import annotations

from pathlib import Path

from .agent_prompts import build_work_packet_agent_prompt
from .coordination import (
    load_active_step,
    load_events,
    summarize_standing_user_instructions,
    sync_active_step_from_packet,
)
from .execution import load_case, load_runner
from .experiments import experiment_status
from .resume import build_resume_payload
from .runtime import build_next_payload
from .workspace import load_json, load_run_spec, resolve_run_dir


def build_work_packet(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    next_payload = build_next_payload(run_dir)
    phase = next_payload["phase"]

    if phase == "draft":
        packet = _draft_packet(run_dir, next_payload)
    elif phase == "awaiting_confirmation":
        packet = _confirmation_packet(run_dir, next_payload)
    elif phase in {"baseline_in_progress", "step_in_progress"}:
        packet = _experiment_packet(run_dir, next_payload)
    elif phase == "paused":
        packet = _paused_packet(run_dir)
    elif phase == "replan_required":
        packet = _replan_packet(run_dir, next_payload)
    elif phase == "complete":
        packet = _complete_packet(run_dir, next_payload)
    else:
        packet = _transition_packet(run_dir, next_payload)

    return _attach_operational_context(run_dir, packet)


def _draft_packet(run_dir: Path, next_payload: dict) -> dict:
    suggestion_files = [
        run_dir / "evals" / "checks.draft.yaml",
        run_dir / "evals" / "judge.draft.md",
        run_dir / "cases" / "train.draft.jsonl",
        run_dir / "cases" / "holdout.draft.jsonl",
    ]
    existing_suggestions = [str(path.resolve()) for path in suggestion_files if path.exists()]
    spec = load_json(run_dir / "spec.json")
    target_path = spec.get("target", {}).get("object_path")
    alignment_plan_path = run_dir / "reports" / "eval_alignment_plan.md"

    tasks = [
        {
            "type": "alignment_plan",
            "instruction": "Use the host agent's plan mode if available, ask any missing eval-alignment questions the way the host normally would with the user, and write the resulting detailed plan to reports/eval_alignment_plan.md.",
            "target": str(alignment_plan_path.resolve()),
        },
        {
            "type": "artifact",
            "instruction": "Replace any placeholder target content with the real artifact under optimization, then commit it.",
            "target": target_path,
        },
        {
            "type": "checks",
            "instruction": "Refine eval checks into 3-5 binary checks in evals/checks.yaml.",
            "target": str((run_dir / "evals" / "checks.yaml").resolve()),
            "starter": str((run_dir / "evals" / "checks.draft.yaml").resolve())
            if (run_dir / "evals" / "checks.draft.yaml").exists()
            else None,
        },
        {
            "type": "judge",
            "instruction": "Refine evals/judge.md into a stable overall judge prompt.",
            "target": str((run_dir / "evals" / "judge.md").resolve()),
            "starter": str((run_dir / "evals" / "judge.draft.md").resolve())
            if (run_dir / "evals" / "judge.draft.md").exists()
            else None,
        },
        {
            "type": "rubric",
            "instruction": "Refine evals/rubric.yaml into weighted criteria, tradeoffs, and non-negotiables that reflect user preferences.",
            "target": str((run_dir / "evals" / "rubric.yaml").resolve()),
        },
        {
            "type": "calibration",
            "instruction": "Populate evals/calibration.jsonl with a few labeled good/bad examples and brief rationales that calibrate evaluator taste.",
            "target": str((run_dir / "evals" / "calibration.jsonl").resolve()),
        },
        {
            "type": "train_cases",
            "instruction": "Populate cases/train.jsonl with at least 3 representative train cases.",
            "target": str((run_dir / "cases" / "train.jsonl").resolve()),
            "starter": str((run_dir / "cases" / "train.draft.jsonl").resolve())
            if (run_dir / "cases" / "train.draft.jsonl").exists()
            else None,
        },
        {
            "type": "holdout_cases",
            "instruction": "Populate cases/holdout.jsonl with at least 2 distinct holdout cases.",
            "target": str((run_dir / "cases" / "holdout.jsonl").resolve()),
            "starter": str((run_dir / "cases" / "holdout.draft.jsonl").resolve())
            if (run_dir / "cases" / "holdout.draft.jsonl").exists()
            else None,
        },
    ]

    packet = {
        "run_dir": str(run_dir),
        "phase": "draft",
        "work_type": "draft_eval_setup",
        "summary": "The run still needs a durable user-aligned eval plan and a stable target/eval/case setup.",
        "required_reads": next_payload["required_reads"],
        "suggestion_files": existing_suggestions,
        "alignment_plan_path": str(alignment_plan_path.resolve()),
        "tasks": tasks,
        "done_when": next_payload["success_condition"],
        "recommended_commands": [
            f"digivolve draft-evals {run_dir}",
            f"digivolve run-loop {run_dir}",
        ],
    }
    packet["execution_steps"] = [task["instruction"] for task in tasks]
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _experiment_packet(run_dir: Path, next_payload: dict) -> dict:
    status = experiment_status(run_dir)
    runner = load_runner(run_dir, experiment_id=status["experiment_id"])
    manifest = load_json(Path(status["manifest_path"]))

    pending_cases = [
        case
        for case in status["cases"]
        if not case["status"]["ready"]
    ]
    case_tasks = []
    for case in pending_cases:
        case_payload = load_case(
            run_dir,
            case["id"],
            experiment_id=status["experiment_id"],
            split=case["split"],
        )
        case_bundle = case_payload["case"]
        commands = [
            f"digivolve case {run_dir} {case['id']} --split {case['split']}",
            f"digivolve case-evals {run_dir} {case['id']} --split {case['split']}",
        ]
        if runner["evaluation_contract"]["mode"] == "external_panel":
            commands.append(
                f"digivolve openrouter-panel-eval {run_dir} {case['id']} --split {case['split']}"
            )
        commands.extend(
            [
                (
                    f"digivolve record-eval {run_dir} {case['id']} --split {case['split']} "
                    f"--check-id {unit['id']} --evaluator-id <id> --evaluator-kind <kind> "
                    "[--model-name <model>] [--passed|--failed] --notes '...'"
                )
                for unit in case_bundle.get("evaluation_units", [])
            ]
        )
        commands.extend(
            [
                f"digivolve finalize-case {run_dir} {case['id']} --split {case['split']}",
                f"digivolve validate-case {run_dir} {case['id']} --split {case['split']}",
            ]
        )
        case_tasks.append(
            {
                "case_id": case["id"],
                "split": case["split"],
                "bundle_file": case.get("bundle_file"),
                "brief_file": case.get("brief_file"),
                "evaluation_units": case_bundle.get("evaluation_units", []),
                "execution_steps": case_bundle.get("execution_steps", []),
                "agent_prompt": case_bundle.get("agent_prompt"),
                "commands": commands,
            }
        )

    summary_command = (
        f"digivolve record-summary {run_dir} --summary '...'"
        if status["kind"] == "baseline"
        else f"digivolve record-summary {run_dir} --summary '...' --mutation-description '...'"
    )

    packet = {
        "run_dir": str(run_dir),
        "phase": next_payload["phase"],
        "work_type": "experiment_execution",
        "summary": f"Complete the active {status['kind']} experiment by filling pending cases and summary.",
        "runner_path": status["runner_path"],
        "manifest_path": status["manifest_path"],
        "summary_path": status["summary_path"],
        "adapter": status["adapter"],
        "pending_cases": case_tasks,
        "pending_case_count": len(case_tasks),
        "check_ids": runner["check_ids"],
        "execution_steps": runner.get("execution_steps", []),
        "done_when": status["summary_status"]["reason"]
        if status["finalizable"]
        else "All pending cases are ready and the summary file is complete.",
        "recommended_commands": [
            f"digivolve runner {run_dir}",
            f"digivolve experiment-status {run_dir}",
            summary_command,
            f"digivolve complete-experiment {run_dir}",
        ],
    }

    target_path = runner.get("workspace", {}).get("target_path")
    worktree_path = runner.get("workspace", {}).get("worktree_path")
    if target_path:
        packet["target_path"] = target_path
    if worktree_path:
        packet["worktree_path"] = worktree_path
        packet["mutation_instruction"] = (
            "Before recording case results, make exactly one mutation in the experiment worktree and commit it."
        )

    if manifest["kind"] == "baseline":
        packet["mutation_instruction"] = "Use the current committed target as-is. Do not mutate it during baseline."

    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _confirmation_packet(run_dir: Path, next_payload: dict) -> dict:
    spec = load_run_spec(run_dir)
    review_files = [
        run_dir / "reports" / "eval_alignment_plan.md",
        run_dir / "reports" / "eval_traceability.md",
        run_dir / "reports" / "eval_draft.md",
        run_dir / "reports" / "eval_review.md",
        run_dir / "reports" / "eval_review_prompt.md",
        run_dir / "reports" / "eval_explained.md",
        run_dir / "evals" / "checks.yaml",
        run_dir / "evals" / "judge.md",
        run_dir / "evals" / "rubric.yaml",
        run_dir / "evals" / "calibration.jsonl",
        run_dir / "cases" / "train.jsonl",
        run_dir / "cases" / "holdout.jsonl",
    ]
    review_questions = [
        "Does the detailed alignment plan faithfully capture what the user actually meant, including open questions, assumptions, tradeoffs, and anti-gaming concerns?",
        "Does the final eval package fully materialize that alignment plan instead of replacing it with a much simpler generic eval?",
        "Do these checks match what success means to the user?",
        "Does the rubric encode the right weights, tradeoffs, and non-negotiables?",
        "Do the calibration examples capture what the user considers good and bad output?",
        "Could the artifact game any check without truly improving?",
        "Are the train cases representative of common usage?",
        "Are the holdout cases distinct enough to test transfer?",
        "Should evaluation use a built-in subagent or an external panel?",
        "If subagent-based, is the selected host system the right one?",
        "If the evaluator path is not already fixed, have we explicitly asked the user to choose it instead of defaulting it ourselves?",
        "What would make the user comfortable saying `start baseline`?",
    ]
    packet = {
        "run_dir": str(run_dir),
        "phase": "awaiting_confirmation",
        "work_type": "confirmation_review",
        "summary": "Review the drafted eval package and evaluator strategy with the user before starting baseline.",
        "required_reads": next_payload["required_reads"],
        "review_files": [str(path.resolve()) for path in review_files if path.exists()],
        "alignment_plan_path": str((run_dir / "reports" / "eval_alignment_plan.md").resolve()),
        "traceability_path": str((run_dir / "reports" / "eval_traceability.md").resolve()),
        "review_questions": review_questions,
        "evaluator_strategy": _evaluator_strategy(spec),
        "evaluator_options": _evaluator_options(),
        "done_when": next_payload["success_condition"],
        "recommended_commands": [
            f"digivolve configure-evaluators {run_dir} --mode <subagent|external_panel>",
            f"digivolve confirm-evals {run_dir}",
            f"digivolve draft-evals {run_dir}",
        ],
    }
    packet["execution_steps"] = [
        "Review the detailed eval alignment plan with the user before compressing anything into approval language.",
        "Explain the eval package to the user in plain language before asking for approval.",
        "Show the current checks, pass/fail conditions, judge prompt, rubric, calibration examples, cases, and evaluator strategy truthfully and fully rather than only summarizing them.",
        "If the evaluator path is not already fixed by the run artifacts, explicitly ask the user to choose it before proceeding.",
        "Suggest stronger alternatives, missing failure modes, and better calibration examples from multiple angles.",
        "Wait for explicit user approval before baseline.",
        "If the user requests edits, revise the eval files and rerun `draft-evals`.",
        "When the user explicitly approves, run `confirm-evals`.",
    ]
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _evaluator_strategy(spec: dict) -> dict:
    evaluation = spec.get("evaluation", {})
    mode = evaluation.get("evaluator_mode", "subagent")
    panel_size = max(1, int(evaluation.get("panel_size", 1)))
    if mode == "subagent":
        system = evaluation.get("subagent_system", "codex")
        return {
            "mode": "subagent",
            "label": "built-in subagent",
            "host_system": system,
            "model_policy": evaluation.get("subagent_model_policy", "best_available"),
            "required_evaluators": panel_size,
            "discussion_note": (
                "Discuss with the user whether this host system should own independent evaluation."
            ),
        }
    return {
        "mode": "external_panel",
        "label": "external panel",
        "required_evaluators": panel_size,
        "external_agents": list(evaluation.get("external_agents", [])),
        "discussion_note": "Discuss with the user which external evaluators should be used.",
    }


def _evaluator_options() -> list[dict]:
    return [
        {
            "mode": "subagent",
            "label": "Use the host system's built-in subagent capability",
            "examples": ["codex", "claude-code", "opencode"],
        },
        {
            "mode": "external_panel",
            "label": "Use a user-selected external evaluator panel",
            "examples": ["openrouter-panel", "manual multi-agent panel"],
        },
    ]


def _paused_packet(run_dir: Path) -> dict:
    resume_payload = build_resume_payload(run_dir, activate=False, limit=3)
    packet = {
        "run_dir": str(run_dir),
        "phase": "paused",
        "work_type": "resume",
        "summary": "The run is paused and must be resumed before more work can happen.",
        "resume_payload": resume_payload,
        "recommended_commands": [
            f"digivolve resume {run_dir}",
            f"digivolve resume {run_dir} --activate",
        ],
    }
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _complete_packet(run_dir: Path, next_payload: dict) -> dict:
    packet = {
        "run_dir": str(run_dir),
        "phase": "complete",
        "work_type": "reporting",
        "summary": "The run is complete. Only reporting work remains.",
        "required_reads": next_payload["required_reads"],
        "recommended_commands": [
            f"digivolve report {run_dir}",
        ],
    }
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _replan_packet(run_dir: Path, next_payload: dict) -> dict:
    state = load_json(run_dir / "state.json")
    packet = {
        "run_dir": str(run_dir),
        "phase": "replan_required",
        "work_type": "replan",
        "summary": "A user change invalidated the current step. Reconcile the new direction before continuing.",
        "required_reads": next_payload["required_reads"],
        "replan_reason": state.get("replan_reason"),
        "done_when": next_payload["success_condition"],
        "recommended_commands": [
            f"digivolve replan {run_dir} --summary '...'",
            f"digivolve work-packet {run_dir}",
        ],
    }
    packet["execution_steps"] = [
        "Read the latest event log and active step snapshot to understand what was interrupted or invalidated.",
        "Update the relevant run artifacts so the new direction is reflected on disk.",
        "Record the reconciled direction with `digivolve replan ...` before resuming execution.",
    ]
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _transition_packet(run_dir: Path, next_payload: dict) -> dict:
    packet = {
        "run_dir": str(run_dir),
        "phase": next_payload["phase"],
        "work_type": "transition",
        "summary": "The run can continue through deterministic harness transitions.",
        "required_reads": next_payload["required_reads"],
        "done_when": next_payload["success_condition"],
        "recommended_commands": [
            f"digivolve advance {run_dir}",
            f"digivolve run-loop {run_dir}",
        ],
    }
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet


def _attach_operational_context(run_dir: Path, packet: dict) -> dict:
    if packet.get("phase") not in {"paused", "replan_required"}:
        packet["active_step"] = sync_active_step_from_packet(run_dir, packet)
    else:
        packet["active_step"] = load_active_step(run_dir)
    packet["recent_events"] = load_events(run_dir, limit=5)
    packet["standing_user_instructions"] = summarize_standing_user_instructions(
        load_events(run_dir, limit=10)
    )
    packet["agent_prompt"] = build_work_packet_agent_prompt(packet)
    return packet
