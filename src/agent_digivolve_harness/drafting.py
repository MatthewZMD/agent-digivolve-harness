from __future__ import annotations

import json
from pathlib import Path

from .evaluation import load_checks
from .readiness import assess_run_readiness, readiness_recommendations
from .workspace import load_run_spec, load_run_state, resolve_run_dir, save_run_state


def draft_evals(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    state = load_run_state(run_dir)
    readiness = assess_run_readiness(run_dir)
    ready_for_baseline = readiness["ready_for_baseline"]
    require_confirmation = spec.get("evaluation", {}).get("require_confirmation", True)
    artifact_preview = _artifact_preview(run_dir, spec)
    suggestion_files = _write_draft_suggestions(run_dir, spec, readiness, artifact_preview)

    report_path = run_dir / "reports" / "eval_draft.md"
    report_path.write_text(
        _build_eval_draft_report(run_dir, spec, readiness, artifact_preview, suggestion_files),
        encoding="utf-8",
    )
    review_files = _write_eval_review_materials(
        run_dir,
        spec,
        readiness,
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
) -> str:
    recommendations = "\n".join(
        f"- {item}" for item in readiness_recommendations(readiness)
    )
    readiness_lines = "\n".join(
        [
            _readiness_line("artifact", readiness["artifact"]),
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
        "## Artifact Preview\n\n"
        f"```text\n{artifact_preview}\n```\n\n"
        "## Drafting Rules\n\n"
        "- Keep checks binary.\n"
        "- Aim for 3-5 checks total.\n"
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

    return written


def _write_eval_review_materials(
    run_dir: Path,
    spec: dict,
    readiness: dict[str, object],
    *,
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
        _build_eval_review_report(spec, checks, train_cases, holdout_cases),
        encoding="utf-8",
    )
    prompt_path.write_text(
        _build_eval_review_prompt(spec, checks, train_cases, holdout_cases),
        encoding="utf-8",
    )
    explained_path.write_text(
        _build_eval_explained_report(spec, checks, train_cases, holdout_cases),
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


def _cases_draft(spec: dict, artifact_preview: str, *, split: str) -> str:
    prompts = _starter_case_inputs(spec["artifact_type"], spec["goal"], artifact_preview, split)
    rows = []
    for index, prompt in enumerate(prompts, start=1):
        rows.append(json.dumps({"id": f"{split}-{index}", "input": prompt}))
    return "\n".join(rows) + "\n"


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


def _build_eval_review_report(
    spec: dict,
    checks: list[dict[str, str]],
    train_cases: list[dict[str, str]],
    holdout_cases: list[dict[str, str]],
) -> str:
    check_lines = "\n".join(
        f"- `{check['id']}`: {check['question']}"
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
        "## Current Checks\n\n"
        f"{check_lines}\n\n"
        "## Train Cases\n\n"
        f"{train_lines}\n\n"
        "## Holdout Cases\n\n"
        f"{holdout_lines}\n\n"
        "## Evaluator Strategy\n\n"
        f"{_evaluator_review_block(spec)}\n\n"
        "## Review Angles\n\n"
        "- Do these checks reflect what success actually means to the user?\n"
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
) -> str:
    check_lines = "\n".join(
        f"- {check['id']}: {check['question']}"
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
        "Before asking for approval, explain the eval package in plain language:\n"
        "- what this eval is actually testing\n"
        "- why each check exists\n"
        "- why there are both train and holdout cases\n"
        "- what baseline means in this run\n\n"
        "Checks:\n"
        f"{check_lines}\n\n"
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
        "## What The Checks Are Really Testing\n\n"
        f"{check_lines}\n\n"
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


def _evaluator_review_block(spec: dict) -> str:
    evaluation = spec.get("evaluation", {})
    mode = evaluation.get("evaluator_mode", "subagent")
    panel_size = max(1, int(evaluation.get("panel_size", 1)))
    if mode == "subagent":
        system = evaluation.get("subagent_system", "codex")
        return (
            f"- mode: built-in subagent\n"
            f"- host_system: `{system}`\n"
            f"- required_evaluators: `{panel_size}`\n"
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
