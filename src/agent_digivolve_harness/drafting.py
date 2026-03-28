from __future__ import annotations

import json
from pathlib import Path

from .evaluation import (
    calibration_file,
    design_calibration_examples,
    design_rubric_template,
    format_calibration_examples,
    load_calibration_examples,
    load_checks,
    load_support_text,
    looks_design_oriented,
    rubric_file,
)
from .readiness import assess_run_readiness, readiness_recommendations
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state
from .yaml_utils import dump_yaml


def draft_evals(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)
    ready_for_baseline = readiness["ready_for_baseline"]
    require_confirmation = spec.get("evaluation", {}).get("require_confirmation", True)
    artifact_preview = _artifact_preview(run_dir, spec)
    alignment_plan_text = _load_alignment_plan_text(run_dir / "reports" / "eval_alignment_plan.md")
    judge_text = load_support_text(run_dir / spec["evaluation"]["judge_file"])
    rubric_text = load_support_text(run_dir / rubric_file(spec))
    calibration_examples = load_calibration_examples(run_dir / calibration_file(spec), limit=3)
    suggestion_files = _write_draft_suggestions(run_dir, spec, readiness, artifact_preview)
    checks = load_checks(run_dir / spec["evaluation"]["checks_file"])
    train_cases = _load_case_inputs(run_dir / "cases" / "train.jsonl")
    holdout_cases = _load_case_inputs(run_dir / "cases" / "holdout.jsonl")

    report_path = run_dir / "reports" / "eval_draft.md"
    report_path.write_text(
        _build_eval_draft_report(
            run_dir,
            spec,
            readiness,
            artifact_preview,
            suggestion_files,
            alignment_plan_text=alignment_plan_text,
            rubric_text=rubric_text,
            calibration_examples=calibration_examples,
        ),
        encoding="utf-8",
    )
    traceability_path = run_dir / "reports" / "eval_traceability.md"
    traceability_path.write_text(
        _build_eval_traceability_report(
            spec,
            checks,
            train_cases,
            holdout_cases,
            alignment_plan_text=alignment_plan_text,
            judge_text=judge_text,
            rubric_text=rubric_text,
            calibration_examples=calibration_examples,
        ),
        encoding="utf-8",
    )
    review_files = _write_eval_review_materials(
        run_dir,
        spec,
        readiness,
        alignment_plan_text=alignment_plan_text,
        judge_text=judge_text,
        rubric_text=rubric_text,
        calibration_examples=calibration_examples,
        ready_for_baseline=ready_for_baseline,
        require_confirmation=require_confirmation,
    )

    state["eval_drafted"] = True
    if ready_for_baseline:
        if require_confirmation:
            state["status"] = "awaiting_confirmation"
            state["eval_confirmed"] = False
        else:
            state["status"] = "ready"
            state["eval_confirmed"] = True
    else:
        state["status"] = "draft"
        state["eval_confirmed"] = False
    save_run_state(run_dir, state)

    written_files = [str(report_path), str((run_dir / "state.json").resolve())]
    written_files.append(str(traceability_path.resolve()))
    written_files.extend(str(path.resolve()) for path in suggestion_files)
    written_files.extend(str(path.resolve()) for path in review_files)

    return {
        "run_dir": str(run_dir),
        "run_id": spec["run_id"],
        "state_status": state["status"],
        "ready_for_baseline": ready_for_baseline,
        "readiness": readiness,
        "recommendations": readiness_recommendations(readiness),
        "written_files": written_files,
        "next_action": (
            "confirm_evals"
            if ready_for_baseline and require_confirmation
            else ("run_baseline" if ready_for_baseline else "draft_evals")
        ),
    }


def _build_eval_draft_report(
    run_dir: Path,
    spec: dict,
    readiness: dict[str, object],
    artifact_preview: str,
    suggestion_files: list[Path],
    *,
    alignment_plan_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
) -> str:
    recommendations = "\n".join(
        f"- {item}" for item in readiness_recommendations(readiness)
    )
    readiness_lines = "\n".join(
        [
            _readiness_line("artifact", readiness["artifact"]),
            _readiness_line("alignment_plan", readiness["alignment_plan"]),
            _readiness_line("checks", readiness["checks"]),
            _readiness_line("judge", readiness["judge"]),
            _readiness_line("train_cases", readiness["train_cases"]),
            _readiness_line("holdout_cases", readiness["holdout_cases"]),
        ]
    )
    suggestions = "\n".join(
        f"- `{path.relative_to(run_dir)}`" for path in suggestion_files
    ) or "- no draft suggestions were needed"

    return (
        "# Eval Draft\n\n"
        f"## Run\n\n"
        f"- run_id: `{spec['run_id']}`\n"
        f"- artifact_type: `{spec['artifact_type']}`\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Readiness\n\n"
        f"{readiness_lines}\n\n"
        "## Recommendations\n\n"
        f"{recommendations}\n\n"
        "## Draft Files\n\n"
        f"{suggestions}\n\n"
        "## Eval Alignment Plan Preview\n\n"
        f"```text\n{alignment_plan_text or '<alignment plan missing>'}\n```\n\n"
        "## User Calibration Files\n\n"
        f"- rubric: `{rubric_file(spec)}`\n"
        f"- calibration: `{calibration_file(spec)}`\n\n"
        "These files exist to encode user preference, tradeoffs, and labeled examples before baseline.\n\n"
        "## Rubric Preview\n\n"
        f"```text\n{rubric_text or '<rubric file missing>'}\n```\n\n"
        "## Calibration Examples\n\n"
        f"{format_calibration_examples(calibration_examples)}\n\n"
        "## Artifact Preview\n\n"
        f"```text\n{artifact_preview}\n```\n\n"
        "## Drafting Rules\n\n"
        "- Keep checks binary.\n"
        "- Aim for 3-5 checks total.\n"
        "- If user guidance is sparse, still propose a best-effort eval package derived from the goal and artifact type.\n"
        "- For subjective tasks, break quality into several parts instead of one vague overall score.\n"
        "- For design-heavy tasks, a strong first-pass rubric often separates design quality, originality, craft, and functionality.\n"
        "- Use the rubric to capture weighted preferences, tradeoffs, and non-negotiables.\n"
        "- Use calibration examples to show what good and bad look like in practice.\n"
        "- Keep holdout distinct from train cases.\n"
        "- Use the judge prompt to catch checklist gaming and regressions in quality.\n"
    )


def _artifact_preview(run_dir: Path, spec: dict) -> str:
    target_path = spec.get("target", {}).get("object_path")
    if not target_path:
        return "<no target path>"

    path = Path(target_path).expanduser().resolve()
    if not path.exists():
        return "<target file missing>"
    if path.is_dir():
        return f"<directory target at {path}>"

    lines = path.read_text(encoding="utf-8").splitlines()
    preview = "\n".join(lines[:20]).strip()
    return preview or "<target is empty>"


def _readiness_line(name: str, payload: dict) -> str:
    return f"- `{name}`: {'ready' if payload['ready'] else 'not ready'} ({payload['reason']})"


def _write_draft_suggestions(
    run_dir: Path,
    spec: dict,
    readiness: dict[str, object],
    artifact_preview: str,
) -> list[Path]:
    written: list[Path] = []

    if not readiness["checks"]["ready"]:
        path = run_dir / "evals" / "checks.draft.yaml"
        path.write_text(_checks_draft(spec), encoding="utf-8")
        written.append(path)
    if not readiness["alignment_plan"]["ready"]:
        path = run_dir / "reports" / "eval_alignment_plan.md"
        if not path.exists() or "<replace" in path.read_text(encoding="utf-8").lower():
            path.write_text(_alignment_plan_draft(spec), encoding="utf-8")
        written.append(path)
    if not readiness["judge"]["ready"]:
        path = run_dir / "evals" / "judge.draft.md"
        path.write_text(_judge_draft(spec), encoding="utf-8")
        written.append(path)
    if not readiness["train_cases"]["ready"]:
        path = run_dir / "cases" / "train.draft.jsonl"
        path.write_text(_cases_draft(spec, artifact_preview, split="train"), encoding="utf-8")
        written.append(path)
    if not readiness["holdout_cases"]["ready"]:
        path = run_dir / "cases" / "holdout.draft.jsonl"
        path.write_text(_cases_draft(spec, artifact_preview, split="holdout"), encoding="utf-8")
        written.append(path)
    rubric_path = run_dir / rubric_file(spec)
    if not rubric_path.exists():
        rubric_path.write_text(_rubric_draft(spec), encoding="utf-8")
        written.append(rubric_path)
    calibration_path = run_dir / calibration_file(spec)
    if not calibration_path.exists():
        calibration_path.write_text(_calibration_draft(spec), encoding="utf-8")
        written.append(calibration_path)

    return written


def _write_eval_review_materials(
    run_dir: Path,
    spec: dict,
    readiness: dict[str, object],
    *,
    alignment_plan_text: str,
    judge_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
    ready_for_baseline: bool,
    require_confirmation: bool,
) -> list[Path]:
    if not ready_for_baseline or not require_confirmation:
        return []

    checks = load_checks(run_dir / spec["evaluation"]["checks_file"])
    train_cases = _load_case_inputs(run_dir / "cases" / "train.jsonl")
    holdout_cases = _load_case_inputs(run_dir / "cases" / "holdout.jsonl")
    review_path = run_dir / "reports" / "eval_review.md"
    prompt_path = run_dir / "reports" / "eval_review_prompt.md"
    explained_path = run_dir / "reports" / "eval_explained.md"

    review_path.write_text(
        _build_eval_review_report(
            spec,
            checks,
            train_cases,
            holdout_cases,
            alignment_plan_text=alignment_plan_text,
            judge_text=judge_text,
            rubric_text=rubric_text,
            calibration_examples=calibration_examples,
        ),
        encoding="utf-8",
    )
    prompt_path.write_text(
        _build_eval_review_prompt(
            spec,
            checks,
            train_cases,
            holdout_cases,
            alignment_plan_text=alignment_plan_text,
            judge_text=judge_text,
            rubric_text=rubric_text,
            calibration_examples=calibration_examples,
        ),
        encoding="utf-8",
    )
    explained_path.write_text(
        _build_eval_explained_report(
            spec,
            checks,
            train_cases,
            holdout_cases,
            alignment_plan_text=alignment_plan_text,
            rubric_text=rubric_text,
            calibration_examples=calibration_examples,
        ),
        encoding="utf-8",
    )
    return [review_path, prompt_path, explained_path]


def _checks_draft(spec: dict) -> str:
    checks = _starter_checks(spec["artifact_type"])
    lines = ["checks:"]
    for check in checks:
        lines.extend(
            [
                "  -",
                f"    id: {check['id']}",
                f"    question: {check['question']}",
                f"    pass: {check['pass']}",
                f"    fail: {check['fail']}",
            ]
        )
    return "\n".join(lines) + "\n"


def _judge_draft(spec: dict) -> str:
    frozen_rules = spec.get("constraints", {}).get("frozen_rules", [])
    rule_lines = "\n".join(f"- {rule}" for rule in frozen_rules) or "- Keep frozen rules intact."
    return (
        "# Judge Draft\n\n"
        "Use this prompt as the fixed judge instruction for every case.\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Judge Rules\n\n"
        "- First score the hard checks exactly as written.\n"
        "- Then look for checklist gaming, regressions, and obvious quality failures.\n"
        "- Use the same bar for train and holdout cases.\n"
        "- Keep the evaluation tied to the actual task, not generic style preferences.\n\n"
        "## Frozen Rules\n\n"
        f"{rule_lines}\n"
    )


def _alignment_plan_draft(spec: dict) -> str:
    return (
        "# Eval Alignment Plan\n\n"
        "Use the host agent's plan mode for eval alignment if available, then replace this draft with a detailed durable plan.\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## User Goal In Plain Language\n\n"
        "<replace with the actual optimization target in plain language>\n\n"
        "## Questions Asked And Answers Learned\n\n"
        "- Q: <replace>\n"
        "  A: <replace>\n\n"
        "## Remaining Unknowns And Working Assumptions\n\n"
        "- <replace>\n\n"
        "## Evaluation Design Plan\n\n"
        "### What counts as success\n\n"
        "- <replace>\n\n"
        "### Hard failures and non-negotiables\n\n"
        "- <replace>\n\n"
        "### Weighted preferences and tradeoffs\n\n"
        "- <replace>\n\n"
        "### Anti-gaming and failure modes to catch\n\n"
        "- <replace>\n\n"
        "### Planned train cases\n\n"
        "- <replace>\n\n"
        "### Planned holdout cases\n\n"
        "- <replace>\n\n"
        "### Evaluator strategy and independence plan\n\n"
        "- <replace>\n\n"
        "## Traceability Checklist\n\n"
        "- Planned requirement: <replace> -> Eval artifact: <replace>\n"
    )


def _cases_draft(spec: dict, artifact_preview: str, *, split: str) -> str:
    prompts = _starter_case_inputs(spec["artifact_type"], spec["goal"], artifact_preview, split)
    rows = []
    for index, prompt in enumerate(prompts, start=1):
        rows.append(json.dumps({"id": f"{split}-{index}", "input": prompt}))
    return "\n".join(rows) + "\n"


def _rubric_draft(spec: dict) -> str:
    if spec["artifact_type"] == "repo-task" and looks_design_oriented(spec["goal"]):
        return dump_yaml(design_rubric_template()) + "\n"

    return (
        "criteria:\n"
        "  -\n"
        "    id: primary_success\n"
        "    weight: 3\n"
        "    priority: must\n"
        "    guidance: Replace this with the main thing the user values most.\n"
        "non_negotiables:\n"
        "  - Replace this with a hard constraint the evaluator must never ignore.\n"
        "tradeoffs:\n"
        "  - Replace this with a concrete tradeoff rule such as correctness over style.\n"
    )


def _calibration_draft(spec: dict) -> str:
    if spec["artifact_type"] == "repo-task" and looks_design_oriented(spec["goal"]):
        return "\n".join(json.dumps(row) for row in design_calibration_examples()) + "\n"

    rows = [
        {
            "id": "good-1",
            "label": "good",
            "input": f"Representative request aligned to: {spec['goal']}",
            "output": "Replace this with a short example the user would consider strong.",
            "why": "Explain specifically why this is good.",
        },
        {
            "id": "bad-1",
            "label": "bad",
            "input": f"Representative request aligned to: {spec['goal']}",
            "output": "Replace this with a short example the user would consider weak.",
            "why": "Explain specifically why this is bad.",
        },
    ]
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def _load_case_inputs(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload = json.loads(line)
        rows.append({"id": payload["id"], "input": payload["input"]})
    return rows


def _load_alignment_plan_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _build_eval_review_report(
    spec: dict,
    checks: list[dict[str, str]],
    train_cases: list[dict[str, str]],
    holdout_cases: list[dict[str, str]],
    *,
    alignment_plan_text: str,
    judge_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
) -> str:
    check_lines = "\n".join(
        (
            f"- `{check['id']}`\n"
            f"  - question: {check['question']}\n"
            f"  - pass: {check['pass']}\n"
            f"  - fail: {check['fail']}"
        )
        for check in checks
    ) or "- no checks loaded"
    train_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in train_cases
    ) or "- no train cases loaded"
    holdout_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in holdout_cases
    ) or "- no holdout cases loaded"

    return (
        "# Eval Review\n\n"
        "This run is structurally ready, but baseline should not start until the user explicitly confirms the eval package.\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Detailed Alignment Plan\n\n"
        f"```text\n{alignment_plan_text or '<alignment plan missing>'}\n```\n\n"
        "## Current Checks\n\n"
        f"{check_lines}\n\n"
        "## Judge Prompt\n\n"
        f"```text\n{judge_text or '<judge prompt missing>'}\n```\n\n"
        "## User Rubric\n\n"
        f"```text\n{rubric_text or '<rubric file missing>'}\n```\n\n"
        "## Calibration Examples\n\n"
        f"{format_calibration_examples(calibration_examples)}\n\n"
        "## Train Cases\n\n"
        f"{train_lines}\n\n"
        "## Holdout Cases\n\n"
        f"{holdout_lines}\n\n"
        "## Evaluator Strategy\n\n"
        f"{_evaluator_review_block(spec)}\n\n"
        "## Review Angles\n\n"
        "- Do these checks reflect what success actually means to the user?\n"
        "- Does this review still faithfully reflect the detailed alignment plan, or did anything get simplified too aggressively?\n"
        "- If the user has not given much guidance yet, is this still the best reasonable first-pass eval package?\n"
        "- Does the rubric encode the right weights, tradeoffs, and non-negotiables?\n"
        "- Do the calibration examples capture the user's bar for good and bad output?\n"
        "- Could the artifact game any check without truly improving?\n"
        "- Do the train cases reflect common usage rather than just easy cases?\n"
        "- Do the holdout cases test transfer rather than repetition?\n"
        "- Is the evaluator path the right one: built-in subagent vs external panel?\n"
        "- If using subagents, is the selected host system the right one?\n"
        "- What would make the user comfortable saying \"start baseline\"?\n"
    )


def _build_eval_review_prompt(
    spec: dict,
    checks: list[dict[str, str]],
    train_cases: list[dict[str, str]],
    holdout_cases: list[dict[str, str]],
    *,
    alignment_plan_text: str,
    judge_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
) -> str:
    check_lines = "\n".join(
        (
            f"- {check['id']}: {check['question']}\n"
            f"  pass: {check['pass']}\n"
            f"  fail: {check['fail']}"
        )
        for check in checks
    )
    train_lines = "\n".join(
        f"- {case['id']}: {case['input']}"
        for case in train_cases
    )
    holdout_lines = "\n".join(
        f"- {case['id']}: {case['input']}"
        for case in holdout_cases
    )
    return (
        "Use this prompt when reviewing the eval package with the user.\n\n"
        f"Goal: {spec['goal']}\n\n"
        "I drafted the eval package and I will not start baseline until you explicitly say to start.\n\n"
        "Read the detailed alignment plan first and keep it visible while you review:\n"
        f"{alignment_plan_text or '<alignment plan missing>'}\n\n"
        "Before asking for approval, explain the eval package in plain language:\n"
        "- what this eval is actually testing\n"
        "- why each check exists\n"
        "- what the rubric is encoding about user preference and tradeoffs\n"
        "- what the calibration examples are teaching the evaluator about good and bad output\n"
        "- why there are both train and holdout cases\n"
        "- what baseline means in this run\n\n"
        "When the user asks for detail, show the exact checks, pass/fail conditions, judge prompt, rubric, calibration examples, and cases rather than hiding them behind a summary.\n\n"
        "If the user has not said much yet, present this as a best-effort starting point rather than pretending the preferences are already known.\n\n"
        "Checks:\n"
        f"{check_lines}\n\n"
        "Judge prompt:\n"
        f"{judge_text or '<judge prompt missing>'}\n\n"
        "Rubric:\n"
        f"{rubric_text or '<rubric file missing>'}\n\n"
        "Calibration examples:\n"
        f"{format_calibration_examples(calibration_examples)}\n\n"
        "Train cases:\n"
        f"{train_lines}\n\n"
        "Holdout cases:\n"
        f"{holdout_lines}\n\n"
        "Evaluator strategy:\n"
        f"{_evaluator_review_block(spec)}\n\n"
        "If the evaluator path is not already fixed by the run artifacts, ask the user to choose it explicitly. Do not silently default to `subagent` or `external_panel`.\n\n"
        "Please tell me one of two things:\n"
        "1. what to change in the checks, judge, cases, or evaluator strategy\n"
        "2. or explicitly say `start baseline`\n\n"
        "I should also suggest improvements from several angles:\n"
        "- whether the current eval package faithfully materializes the detailed alignment plan\n"
        "- whether the package needs a better first-pass decomposition into parts of quality\n"
        "- missing user preferences, non-negotiables, or tradeoffs in the rubric\n"
        "- calibration examples that are too generic, too easy, or mislabeled\n"
        "- missing failure modes\n"
        "- overfitting risk\n"
        "- checks that are too vague or too gameable\n"
        "- train cases that are too easy\n"
        "- holdout cases that are too similar to train\n"
        "- evaluator choice that is too lenient, too coupled to the executor, or the wrong runtime\n"
    )


def _build_eval_explained_report(
    spec: dict,
    checks: list[dict[str, str]],
    train_cases: list[dict[str, str]],
    holdout_cases: list[dict[str, str]],
    *,
    alignment_plan_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
) -> str:
    check_lines = "\n".join(
        (
            f"- `{check['id']}`: In plain language, this asks: {check['question']} "
            f"Pass means: {check['pass']} Fail means: {check['fail']}"
        )
        for check in checks
    ) or "- no checks loaded"
    train_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in train_cases
    ) or "- no train cases loaded"
    holdout_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in holdout_cases
    ) or "- no holdout cases loaded"

    return (
        "# Eval Explained\n\n"
        "## In Plain Language\n\n"
        f"This run is trying to improve the artifact so it better satisfies this goal:\n\n"
        f"{spec['goal']}\n\n"
        "In plain language, the eval is not asking whether the artifact merely sounds different. "
        "It is asking whether the artifact is actually better under a fixed set of questions, without "
        "inventing facts, gaming the checks, or only looking good on one easy prompt.\n\n"
        "## The Detailed Alignment Plan Behind This Eval\n\n"
        f"```text\n{alignment_plan_text or '<alignment plan missing>'}\n```\n\n"
        "## What The Checks Are Really Testing\n\n"
        f"{check_lines}\n\n"
        "## What The Rubric Is Doing\n\n"
        "The rubric is where weighted user preference lives. It tells the evaluator which qualities matter most, "
        "which tradeoffs to make when signals conflict, and which failures are unacceptable even if the hard checks pass.\n\n"
        "When the user has not given much guidance yet, the rubric should still be a serious first-pass proposal rather than an empty placeholder.\n\n"
        f"```text\n{rubric_text or '<rubric file missing>'}\n```\n\n"
        "## Why There Are Calibration Examples\n\n"
        "Calibration examples are small labeled examples of good and bad outputs. They make the evaluation bar less abstract "
        "by showing the evaluator what the user actually likes, dislikes, and considers unacceptable.\n\n"
        f"{format_calibration_examples(calibration_examples)}\n\n"
        "## Why There Are Train Cases\n\n"
        "Train cases are the common situations we expect the artifact to handle well. They are where we "
        "want improvement to show up clearly if a change is actually useful.\n\n"
        f"{train_lines}\n\n"
        "## Why There Are Holdout Cases\n\n"
        "Holdout cases are the unseen sanity check. They exist so the system does not overfit to the exact "
        "wording of the train cases and claim improvement that does not transfer.\n\n"
        f"{holdout_lines}\n\n"
        "## What Baseline Means\n\n"
        "Baseline means we first score the current artifact as-is before making any mutation. That gives us "
        "a real reference point, so later iterations can be judged as genuine improvement, regression, or tie.\n"
    )


def _build_eval_traceability_report(
    spec: dict,
    checks: list[dict[str, str]],
    train_cases: list[dict[str, str]],
    holdout_cases: list[dict[str, str]],
    *,
    alignment_plan_text: str,
    judge_text: str,
    rubric_text: str,
    calibration_examples: list[dict[str, str]],
) -> str:
    check_lines = "\n".join(
        (
            f"- `{check['id']}`\n"
            f"  - question: {check['question']}\n"
            f"  - pass: {check['pass']}\n"
            f"  - fail: {check['fail']}"
        )
        for check in checks
    ) or "- no complete checks loaded"
    train_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in train_cases
    ) or "- no train cases loaded"
    holdout_lines = "\n".join(
        f"- `{case['id']}`: {case['input']}"
        for case in holdout_cases
    ) or "- no holdout cases loaded"
    return (
        "# Eval Traceability\n\n"
        "This report exists so the user and executor can inspect the detailed plan and the full eval package in one place.\n\n"
        "## Goal\n\n"
        f"{spec['goal']}\n\n"
        "## Detailed Alignment Plan\n\n"
        f"```text\n{alignment_plan_text or '<alignment plan missing>'}\n```\n\n"
        "## Current Checks\n\n"
        f"{check_lines}\n\n"
        "## Current Judge Prompt\n\n"
        f"```text\n{judge_text or '<judge prompt missing>'}\n```\n\n"
        "## Current Rubric\n\n"
        f"```text\n{rubric_text or '<rubric file missing>'}\n```\n\n"
        "## Current Calibration Examples\n\n"
        f"{format_calibration_examples(calibration_examples)}\n\n"
        "## Current Train Cases\n\n"
        f"{train_lines}\n\n"
        "## Current Holdout Cases\n\n"
        f"{holdout_lines}\n\n"
        "## Review Rule\n\n"
        "If a major requirement appears in the detailed alignment plan but not in the current checks, judge prompt, rubric, calibration examples, or cases, revise the eval package before baseline.\n"
    )


def _evaluator_review_block(spec: dict) -> str:
    evaluation = spec.get("evaluation", {})
    mode = evaluation.get("evaluator_mode", "subagent")
    panel_size = max(1, int(evaluation.get("panel_size", 1)))
    if mode == "subagent":
        system = evaluation.get("subagent_system", "codex")
        return (
            f"- mode: built-in subagent\n"
            f"- host_system: `{system}`\n"
            f"- subagent_model_policy: `{evaluation.get('subagent_model_policy', 'best_available')}`\n"
            f"- required_evaluators: `{panel_size}`\n"
            "- use the strongest available evaluator model on that host unless the user explicitly chooses otherwise\n"
            "- this means the host system's own subagent capability; today this is often Codex, but the same pattern can map to Claude Code, OpenCode, or similar systems\n"
            "- discuss with the user whether this host system should own evaluation\n"
            "- if that choice is not already fixed, explicitly ask the user to confirm it rather than assuming it"
        )
    external_agents = list(evaluation.get("external_agents", []))
    agents_line = ", ".join(external_agents) if external_agents else "user-selected external agents"
    return (
        f"- mode: external panel\n"
        f"- required_evaluators: `{panel_size}`\n"
        f"- configured_slots: {agents_line}\n"
        "- this mode lets the user pick any independent evaluator panel outside the current executor\n"
        "- discuss with the user which external evaluators should be used\n"
        "- if this path is not already fixed, explicitly ask the user to choose it rather than assuming it"
    )


def _starter_checks(artifact_type: str) -> list[dict[str, str]]:
    if artifact_type == "document-copy":
        return [
            {
                "id": "specificity",
                "question": "Does the copy include at least one concrete, verifiable detail?",
                "pass": "A reader can point to a specific fact, number, or product detail.",
                "fail": "The copy stays generic and only makes vague claims.",
            },
            {
                "id": "cta",
                "question": "Does the ending contain a specific next step?",
                "pass": "The CTA tells the reader what to do next in concrete terms.",
                "fail": "The ending is generic, passive, or missing a clear action.",
            },
            {
                "id": "constraint_integrity",
                "question": "Does the copy avoid invented product facts or unsupported claims?",
                "pass": "Every factual claim stays within the known product constraints.",
                "fail": "The copy invents facts, testimonials, or unsupported outcomes.",
            },
        ]
    if artifact_type == "repo-task":
        return [
            {
                "id": "task_complete",
                "question": "Does the output resolve the requested repository task?",
                "pass": "The requested change or fix is completed end-to-end.",
                "fail": "The task is incomplete, wrong, or only partially addressed.",
            },
            {
                "id": "verification",
                "question": "Does the result include concrete verification?",
                "pass": "The output cites tests, commands, or runtime evidence.",
                "fail": "The change has no trustworthy verification evidence.",
            },
            {
                "id": "scope_control",
                "question": "Does the result stay within the intended repository scope?",
                "pass": "Only relevant files and behaviors are touched.",
                "fail": "The change spills into unrelated files or behavior.",
            },
        ]
    return [
        {
            "id": "format",
            "question": "Does the output follow the requested format exactly?",
            "pass": "Required sections, ordering, and structure are present.",
            "fail": "Required format elements are missing or out of order.",
        },
        {
            "id": "constraint_integrity",
            "question": "Does the output respect the prompt's core constraints?",
            "pass": "No prompt rule is violated in the response.",
            "fail": "The response ignores or breaks a stated prompt rule.",
        },
        {
            "id": "directness",
            "question": "Is the output concise and directly responsive to the task?",
            "pass": "The answer is specific, on-task, and free of obvious filler.",
            "fail": "The answer is vague, bloated, or drifts away from the task.",
        },
    ]


def _starter_case_inputs(
    artifact_type: str,
    goal: str,
    artifact_preview: str,
    split: str,
) -> list[str]:
    preview_hint = _preview_hint(artifact_preview)
    if artifact_type == "document-copy":
        base = [
            f"Write hero copy for this goal: {goal}. {preview_hint}",
            f"Write a sharper CTA section for this goal: {goal}. {preview_hint}",
            f"Rewrite the opening so it is more concrete for this goal: {goal}. {preview_hint}",
        ]
    elif artifact_type == "repo-task":
        base = [
            f"Resolve a representative repository task aligned to: {goal}.",
            f"Handle a second repository task that is similar but slightly broader than: {goal}.",
            f"Handle a failure-focused repository task related to: {goal}.",
        ]
    else:
        base = [
            f"Run the prompt on a straightforward request aligned to: {goal}. {preview_hint}",
            f"Run the prompt on a medium-difficulty request aligned to: {goal}. {preview_hint}",
            f"Run the prompt on an edge-case request aligned to: {goal}. {preview_hint}",
        ]

    if split == "holdout":
        return [
            base[1].replace("aligned to", "generalizing beyond"),
            base[2].replace("aligned to", "generalizing beyond"),
        ]
    return base


def _preview_hint(artifact_preview: str) -> str:
    cleaned = " ".join(artifact_preview.split())
    if not cleaned or cleaned.startswith("<"):
        return ""
    return f"Current artifact hint: {cleaned[:140]}"
