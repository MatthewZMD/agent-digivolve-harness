from __future__ import annotations


def _plain_language_instruction() -> str:
    return (
        "Before taking action, explain the current harness state to the user in plain language: "
        "what is happening now, what you are about to do next, and why that step matters. "
        "Translate harness terms instead of assuming the user understands labels like "
        "`record-eval`, `finalize-case`, `step_in_progress`, or `replan_required`. "
        "After any meaningful state transition, explain the new status in plain language before continuing."
    )


def _standing_user_instruction_block(items: list[str] | None) -> str | None:
    normalized = [item.strip() for item in (items or []) if isinstance(item, str) and item.strip()]
    if not normalized:
        return None
    lines = [
        "Standing user instructions:",
        *[f"- {item}" for item in normalized],
        "Treat these as part of the harness contract unless the user explicitly replaces them.",
    ]
    return "\n".join(lines)


def build_runner_execution_steps(payload: dict) -> list[str]:
    steps = [
        "Read the runner payload, manifest, checks file, and judge prompt before executing cases.",
    ]

    target_path = payload.get("workspace", {}).get("target_path")
    worktree_path = payload.get("workspace", {}).get("worktree_path")
    if payload["experiment_kind"] == "baseline":
        steps.append("Use the current committed target as-is for every case in the baseline run.")
    elif target_path:
        steps.append(
            f"Make exactly one candidate mutation in `{target_path}` inside worktree `{worktree_path}` before running the step cases, then commit it."
        )

    steps.extend(_adapter_steps(payload))
    steps.extend(
        [
            "Record one raw output file for every case.",
            _evaluation_step(payload),
            f"Fill the experiment summary at `{payload['summary_path']}` only after all cases are complete.",
        ]
    )
    return steps


def build_runner_agent_prompt(payload: dict) -> str:
    steps = build_runner_execution_steps(payload)
    step_lines = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    standing_block = _standing_user_instruction_block(payload.get("standing_user_instructions"))

    parts = [
        (
            f"Execute experiment `{payload['experiment_id']}` "
            f"(`{payload['experiment_kind']}`) for the `{payload['adapter']}` adapter."
        ),
        _plain_language_instruction(),
        standing_block,
        f"Run directory: `{payload['run_dir']}`.",
        f"Artifact path: `{payload['artifact_path']}`." if payload.get("artifact_path") else None,
        (
            f"Repository path: `{payload['repository_path']}`."
            if payload.get("repository_path")
            else None
        ),
        f"Checks: `{payload['checks_path']}`.",
        f"Judge: `{payload['judge_path']}`.",
        f"Summary target: `{payload['summary_path']}`.",
        "Follow these steps exactly:",
        step_lines,
    ]
    return "\n".join(part for part in parts if part)


def build_case_execution_steps(payload: dict) -> list[str]:
    case = payload["case"]
    steps = [
        f"Read the case input for `{case['split']}:{case['id']}` and keep the execution aligned to the active artifact.",
    ]

    if payload["experiment_kind"] == "baseline":
        steps.append("Do not mutate the artifact during this case; baseline measures the current committed version.")
    elif payload.get("workspace", {}).get("target_path"):
        steps.append("Use the current committed candidate state consistently for this case.")

    steps.extend(_adapter_steps(payload))
    steps.extend(
        [
            f"Write the raw output to `{case['output_file']}`.",
            _case_evaluation_step(payload),
        ]
    )
    return steps


def build_case_agent_prompt(payload: dict) -> str:
    case = payload["case"]
    steps = build_case_execution_steps(payload)
    step_lines = "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))
    standing_block = _standing_user_instruction_block(payload.get("standing_user_instructions"))

    parts = [
        (
            f"Execute case `{case['split']}:{case['id']}` for experiment `{payload['experiment_id']}` "
            f"(`{payload['experiment_kind']}`)."
        ),
        _plain_language_instruction(),
        standing_block,
        f"Run directory: `{payload['run_dir']}`.",
        f"Adapter: `{payload['adapter']}`.",
        f"Artifact path: `{payload['artifact_path']}`." if payload.get("artifact_path") else None,
        (
            f"Repository path: `{payload['repository_path']}`."
            if payload.get("repository_path")
            else None
        ),
        f"Case kind: `{case['kind']}`.",
        (f"Selector: `{case['selector']}`." if case.get("selector") else None),
        (f"Projection: `{case['projection']}`." if case.get("projection") else None),
        "Case input:",
        case["input"],
        "Follow these steps exactly:",
        step_lines,
    ]
    return "\n".join(part for part in parts if part)


def build_case_evaluator_prompt(payload: dict) -> str:
    contract = payload["evaluation_contract"]
    case = payload["case"]
    best_model_line = ""
    if contract["mode"] == "subagent":
        best_model_line = (
            f"Use the strongest available evaluation model on host `{contract.get('subagent_system', 'codex')}`."
            "\n"
        )
    return (
        f"Independent evaluation for `{case['split']}:{case['id']}` is isolated per check.\n"
        f"Run one evaluator call per check id in `{payload['check_ids']}`.\n"
        f"{best_model_line}"
        f"Use the user rubric at `{payload['rubric_path']}` and calibration examples at `{payload['calibration_path']}` to keep the evaluation bar aligned with user preferences.\n"
        f"For each check, use the matching prompt from `evaluator_prompts`, record one verdict, and do not let one check influence another.\n"
        f"Each check requires `{contract['panel_size']}` independent verdict(s) via mode `{contract['mode']}` before finalization."
    )


def build_case_evaluator_prompts(payload: dict) -> dict[str, str]:
    case = payload["case"]
    contract = payload["evaluation_contract"]
    rubric_text = (payload.get("rubric_text") or "").strip()
    calibration_summary = (payload.get("calibration_summary") or "").strip()
    prompts: dict[str, str] = {}
    for unit in payload["evaluation_units"]:
        external_line = None
        subagent_line = None
        if contract["mode"] == "external_panel" and contract.get("external_agents"):
            external_line = f"Preferred external evaluator slots: {contract['external_agents']}."
        if contract["mode"] == "subagent":
            subagent_line = (
                f"Use the host system's built-in subagent capability for `{contract.get('subagent_system', 'codex')}` "
                "to produce this independent verdict. Use the strongest available model on that host for evaluation."
            )
        if contract["mode"] == "external_panel":
            external_line = (
                (external_line + " " if external_line else "")
                + "If the evaluator does not share this workspace, the caller must inline the raw output, this check definition, the judge prompt, the rubric, and the calibration examples."
            )
        parts = [
            (
                f"Independently evaluate only check `{unit['id']}` for case `{case['split']}:{case['id']}` "
                f"in experiment `{payload['experiment_id']}`."
            ),
            "Do not rewrite the artifact. Do not improve the output. Judge only.",
            "Do not score any other checks. Ignore them completely.",
            f"Read the raw output at `{case['output_file']}`.",
            f"Read the judge prompt at `{payload['judge_path']}`.",
            f"Read the user rubric at `{payload['rubric_path']}`.",
            f"Read the calibration examples at `{payload['calibration_path']}`.",
            f"Check question: {unit['question']}",
            f"Pass condition: {unit['pass']}",
            f"Fail condition: {unit['fail']}",
            "Return only JSON with this shape: {\"passed\": true|false, \"notes\": \"...\"}.",
            (
                f"This isolated verdict is one of `{contract['panel_size']}` independent verdict(s) "
                f"required for check `{unit['id']}` via mode `{contract['mode']}`."
            ),
            ("Rubric excerpt:\n" + rubric_text) if rubric_text and contract["mode"] == "external_panel" else None,
            ("Calibration examples:\n" + calibration_summary)
            if calibration_summary and contract["mode"] == "external_panel"
            else None,
            subagent_line,
            external_line,
        ]
        prompts[unit["id"]] = "\n".join(part for part in parts if part)
    return prompts


def build_work_packet_agent_prompt(packet: dict) -> str:
    work_type = packet["work_type"]
    standing_block = _standing_user_instruction_block(packet.get("standing_user_instructions"))
    standing_prefix = standing_block + "\n" if standing_block else ""

    if work_type == "draft_eval_setup":
        task_lines = "\n".join(
            f"{index}. {task['instruction']} Target: `{task['target']}`."
            + (f" Starter: `{task['starter']}`." if task.get("starter") else "")
            for index, task in enumerate(packet.get("tasks", []), start=1)
        )
        return (
            f"Continue the draft phase for run `{packet['run_dir']}`.\n"
            + _plain_language_instruction()
            + "\n"
            + standing_prefix
            + (
            "Materialize the target, checks, judge prompt, rubric, calibration examples, and cases so the run can reach `ready`.\n"
            "If the user has not given much preference data yet, still propose the strongest first-pass eval package you can from the goal and artifact type.\n"
            + f"{task_lines}\n"
            + f"Done when: {packet['done_when']}"
            )
        )

    if work_type == "experiment_execution":
        pending_cases = packet.get("pending_cases", [])
        case_lines = "\n".join(
            f"- `{item['split']}:{item['case_id']}` via `{item['bundle_file']}`"
            for item in pending_cases
        )
        mutation_line = packet.get("mutation_instruction")
        return (
            f"Continue `{packet['phase']}` for run `{packet['run_dir']}`.\n"
            + _plain_language_instruction()
            + "\n"
            + standing_prefix
            + f"Read the runner at `{packet['runner_path']}` and complete the active experiment.\n"
            + (f"{mutation_line}\n" if mutation_line else "")
            + "Pending cases:\n"
            + case_lines
            + "\n"
            + "For each case, write the raw output first, gather isolated independent evaluator verdicts for every check, finalize the official score, "
            + f"then fill `{packet['summary_path']}` and finalize the experiment."
        )

    if work_type == "confirmation_review":
        review_lines = "\n".join(
            f"- {item}" for item in packet.get("review_questions", [])
        )
        strategy = packet.get("evaluator_strategy", {})
        strategy_lines = []
        if strategy.get("mode") == "subagent":
            strategy_lines.extend(
                [
                    "Current proposed evaluator path:",
                    f"- built-in subagent on host system `{strategy.get('host_system', 'codex')}`",
                    f"- subagent model policy: `{strategy.get('model_policy', 'best_available')}`",
                    f"- required independent verdicts: `{strategy.get('required_evaluators', 1)}`",
                ]
            )
        elif strategy:
            strategy_lines.extend(
                [
                    "Current proposed evaluator path:",
                    "- external evaluator panel",
                    f"- required independent verdicts: `{strategy.get('required_evaluators', 1)}`",
                ]
            )
        strategy_block = "\n".join(strategy_lines)
        return (
            f"Review the eval package for run `{packet['run_dir']}` with the user.\n"
            + _plain_language_instruction()
            + "\n"
            + standing_prefix
            + "Before asking for approval, explain the eval package in plain language: what it is testing, why each check exists, "
            + "what the rubric and calibration examples are doing, why there are both train and holdout cases, and what baseline means.\n"
            + "Do not start baseline until the user explicitly confirms the package.\n"
            + (strategy_block + "\n" if strategy_block else "")
            + "If the evaluator path is not already fixed by the run artifacts, explicitly ask the user to choose it. Do not silently default to `subagent` or `external_panel`.\n"
            + "If the user has not given much feedback yet, present the current package as your best first-pass proposal and invite correction.\n"
            + "If the rubric still feels generic, ask the user for better examples of good and bad outputs before baseline.\n"
            + "Discuss these review questions:\n"
            + f"{review_lines}\n"
            + "If the user changes the evaluator path, record that choice with `configure-evaluators` first.\n"
            + f"When the user is satisfied, run `{packet['recommended_commands'][0]}`."
        )

    if work_type == "resume":
        resume_payload = packet["resume_payload"]
        return resume_payload["agent_prompt"]

    if work_type == "replan":
        return (
            f"Replan run `{packet['run_dir']}` before more execution happens.\n"
            + _plain_language_instruction()
            + "\n"
            + standing_prefix
            + f"Reason: {packet.get('replan_reason') or 'A user changed direction or invalidated the current step.'}\n"
            "Read the event log and active step snapshot first, update the run artifacts to match the new direction, "
            f"then record the new plan with `{packet['recommended_commands'][0]}`."
        )

    if work_type == "reporting":
        return (
            f"Read the final run state for `{packet['run_dir']}` and generate the closing report.\n"
            + _plain_language_instruction()
            + "\n"
            + standing_prefix
            + f"Recommended command: `{packet['recommended_commands'][0]}`."
        )

    return (
        f"Continue run `{packet['run_dir']}` from phase `{packet['phase']}`.\n"
        + _plain_language_instruction()
        + "\n"
        + standing_prefix
        + f"Done when: {packet.get('done_when', 'the next lifecycle step is complete')}."
    )


def _adapter_steps(payload: dict) -> list[str]:
    adapter = payload["adapter"]
    artifact_path = payload.get("artifact_path")
    repository_path = payload.get("repository_path")

    if adapter == "prompt_runner":
        return [
            f"Use the prompt artifact at `{artifact_path}` as the prompt under evaluation.",
            "Apply the prompt to the case input and capture the full raw model response.",
            "Do not score inside the executor. Independent evaluation happens after the raw response is written.",
        ]

    if adapter == "document_copy_runner":
        case = payload.get("case", {})
        case_kind = case.get("kind", "projection_case")
        selector = case.get("selector")
        projection = case.get("projection")
        steps = [
            f"Use the artifact at `{artifact_path}` as the canonical README under evaluation.",
        ]
        if case_kind == "artifact_case":
            steps.append(
                "Do not rewrite the README for this case. Capture the relevant artifact view as evidence and write it to the raw output file."
            )
        elif case_kind == "section_case":
            selector_text = f" using selector `{selector}`" if selector else ""
            steps.append(
                f"Do not rewrite the README for this case. Extract the relevant section from the README{selector_text} and write that evidence to the raw output file."
            )
        else:
            projection_text = f" with projection `{projection}`" if projection else ""
            steps.append(
                f"Produce a derived answer grounded strictly in the README{projection_text}. Preserve product facts and do not invent capabilities."
            )
        steps.append(
            "Do not score inside the executor. Independent evaluation happens after the raw output is written."
        )
        return steps

    return [
        f"Operate inside the repository workspace `{repository_path or '<current workspace>'}`.",
        "Treat the case input as the concrete repo task to execute or analyze.",
        "Write a concise execution result with changed files and verification notes; independent evaluation happens afterwards.",
    ]


def _evaluation_step(payload: dict) -> str:
    contract = payload.get("evaluation_contract", {})
    if contract.get("independent_required"):
        return (
            f"Use `{contract['mode']}` to collect `{contract['panel_size']}` independent evaluator verdict(s) per check "
            "before writing the official score JSON."
        )
    return "Write one official score JSON for every case."


def _case_evaluation_step(payload: dict) -> str:
    contract = payload.get("evaluation_contract", {})
    case = payload["case"]
    if contract.get("independent_required"):
        if contract.get("mode") == "external_panel":
            return (
                f"Do not self-score. Use the external evaluator runner for `{case['evaluation_dir']}` "
                f"to evaluate every check independently (for example `digivolve openrouter-panel-eval <run> {case['id']} --split {case['split']}`), "
                f"then finalize `{case['score_file']}`."
            )
        return (
            f"Do not self-score. Use the per-check evaluator prompts to collect `{contract['panel_size']}` independent verdict(s) per check, "
            f"record them for `{case['evaluation_dir']}`, then finalize `{case['score_file']}`."
        )
    return (
        f"Write the score JSON to `{case['score_file']}` using only check ids "
        f"{payload['check_ids']} and max score `{payload['per_case_max_score']}`."
    )
