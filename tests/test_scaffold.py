from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_digivolve_harness.advance import advance_run
from agent_digivolve_harness.confirmation import confirm_evals
from agent_digivolve_harness.drafting import draft_evals
from agent_digivolve_harness.evaluator_strategy import configure_evaluators
from agent_digivolve_harness.execution import (
    finalize_case,
    list_case_evaluations,
    list_cases,
    load_case,
    load_runner,
    record_case,
    record_eval,
    record_summary,
    validate_case,
)
from agent_digivolve_harness.experiments import complete_experiment, experiment_status
from agent_digivolve_harness.coordination import load_events
from agent_digivolve_harness.interventions import (
    add_run_note,
    change_run_direction,
    interrupt_run,
    resolve_replan,
)
from agent_digivolve_harness.models import InitOptions
import agent_digivolve_harness.baseline as baseline_module
from agent_digivolve_harness.baseline import finalize_baseline, prepare_baseline
from agent_digivolve_harness.journal import load_journal_entries
from agent_digivolve_harness.loop import run_loop
from agent_digivolve_harness.openrouter_eval import openrouter_panel_eval
from agent_digivolve_harness.readiness import assess_run_readiness
from agent_digivolve_harness.pause import pause_run
from agent_digivolve_harness.reporting import generate_report
from agent_digivolve_harness.resume import build_resume_payload
from agent_digivolve_harness.runtime import build_next_payload
from agent_digivolve_harness.scaffold import create_run_scaffold
from agent_digivolve_harness.status_summary import build_status_summary
from agent_digivolve_harness.step import finalize_step, prepare_step
from agent_digivolve_harness.workpack import build_work_packet
from agent_digivolve_harness.runners import build_case_payload
from agent_digivolve_harness.workspace import resolve_run_dir, runs_root, targets_root


class ScaffoldTests(unittest.TestCase):
    def setUp(self) -> None:
        previous_root = os.environ.get("AGENT_DIGIVOLVE_HARNESS_RUNS_ROOT")
        previous_targets_root = os.environ.get("AGENT_DIGIVOLVE_HARNESS_TARGETS_ROOT")
        isolated_root = Path(tempfile.mkdtemp()) / "runs"
        isolated_targets_root = isolated_root.parent / "targets"
        os.environ["AGENT_DIGIVOLVE_HARNESS_RUNS_ROOT"] = str(isolated_root)
        os.environ["AGENT_DIGIVOLVE_HARNESS_TARGETS_ROOT"] = str(isolated_targets_root)
        runs_root().mkdir(parents=True, exist_ok=True)
        targets_root().mkdir(parents=True, exist_ok=True)

        def _restore_env() -> None:
            if previous_root is None:
                os.environ.pop("AGENT_DIGIVOLVE_HARNESS_RUNS_ROOT", None)
            else:
                os.environ["AGENT_DIGIVOLVE_HARNESS_RUNS_ROOT"] = previous_root
            if previous_targets_root is None:
                os.environ.pop("AGENT_DIGIVOLVE_HARNESS_TARGETS_ROOT", None)
            else:
                os.environ["AGENT_DIGIVOLVE_HARNESS_TARGETS_ROOT"] = previous_targets_root

        self.addCleanup(_restore_env)
        self.addCleanup(shutil.rmtree, isolated_root.parent, True)

    def _record_independent_case_result(
        self,
        run_dir: Path,
        *,
        case_id: str,
        split: str,
        output_text: str,
        score: float,
        notes: str,
        passed_checks: list[str],
        failed_checks: list[str],
        experiment_id: int | None = None,
        evaluator_id: str = "judge-1",
        evaluator_kind: str = "subagent",
    ) -> dict:
        case_payload = load_case(run_dir, case_id, experiment_id=experiment_id, split=split)
        output_path = run_dir / case_payload["case"]["case"]["output_file"]
        output_path.write_text(output_text + "\n", encoding="utf-8")
        for check_id in passed_checks:
            record_eval(
                run_dir,
                case_id,
                experiment_id=experiment_id,
                split=split,
                evaluator_id=evaluator_id,
                evaluator_kind=evaluator_kind,
                check_id=check_id,
                passed=True,
                notes=notes,
            )
        for check_id in failed_checks:
            record_eval(
                run_dir,
                case_id,
                experiment_id=experiment_id,
                split=split,
                evaluator_id=evaluator_id,
                evaluator_kind=evaluator_kind,
                check_id=check_id,
                passed=False,
                notes=notes,
            )
        return finalize_case(run_dir, case_id, experiment_id=experiment_id, split=split)

    def _target_path(self, run_dir: Path) -> Path:
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        return Path(spec["target"]["object_path"]).resolve()

    def _repo_root(self, run_dir: Path) -> Path:
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        return Path(spec["target"]["repo_root"]).resolve()

    def _write_target_text(self, run_dir: Path, contents: str) -> None:
        path = self._target_path(run_dir)
        path.write_text(contents, encoding="utf-8")
        self._commit_target(run_dir, "Update target content")

    def _commit_target(self, run_dir: Path, message: str) -> None:
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        repo_root = Path(spec["target"]["repo_root"]).resolve()
        repo_relpath = spec["target"]["repo_relpath"]
        subprocess.run(
            ["git", "-C", str(repo_root), "add", "--all", "--", repo_relpath],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_root), "commit", "-m", message],
            check=True,
            capture_output=True,
            text=True,
        )

    def _active_target_path(self, run_dir: Path) -> Path:
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        return Path(state["git"]["active_target_path"]).resolve()

    def _active_worktree(self, run_dir: Path) -> Path:
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        return Path(state["git"]["active_worktree"]).resolve()

    def _write_step_mutation(self, run_dir: Path, contents: str) -> None:
        target_path = self._active_target_path(run_dir)
        worktree = self._active_worktree(run_dir)
        spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
        repo_relpath = spec["target"]["repo_relpath"]
        target_path.write_text(contents, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(worktree), "add", "--all", "--", repo_relpath],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(worktree), "commit", "-m", "Mutate experiment candidate"],
            check=True,
            capture_output=True,
            text=True,
        )

    def _write_case_results(
        self,
        run_dir: Path,
        experiment_id: int,
        *,
        train_score: float,
        train_max_score: float,
        holdout_score: float,
        holdout_max_score: float,
    ) -> None:
        manifest_path = run_dir / "outputs" / f"exp-{experiment_id:03d}" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        train_cases = [case for case in manifest["cases"] if case["split"] == "train"]
        holdout_cases = [case for case in manifest["cases"] if case["split"] == "holdout"]

        train_allocations = self._allocate_scores(len(train_cases), train_score, train_max_score)
        holdout_allocations = self._allocate_scores(len(holdout_cases), holdout_score, holdout_max_score)

        for case, (score, max_score) in zip(train_cases, train_allocations, strict=True):
            self._write_case_files(run_dir, case, score, max_score)
        for case, (score, max_score) in zip(holdout_cases, holdout_allocations, strict=True):
            self._write_case_files(run_dir, case, score, max_score)

    def _allocate_scores(self, count: int, total_score: float, total_max: float) -> list[tuple[float, float]]:
        if count <= 0:
            return []

        base_max = int(total_max // count)
        max_values = [base_max for _ in range(count)]
        max_values[-1] += int(total_max - sum(max_values))

        base_score = int(total_score // count)
        score_values = [base_score for _ in range(count)]
        score_values[-1] += int(total_score - sum(score_values))

        return list(zip(score_values, max_values, strict=True))

    def _write_case_files(self, run_dir: Path, case: dict, score: float, max_score: float) -> None:
        output_path = run_dir / case["output_file"]
        score_path = run_dir / case["score_file"]
        output_path.write_text(
            f"Output for {case['split']}:{case['id']}\n",
            encoding="utf-8",
        )
        score_path.write_text(
            json.dumps(
                {
                    "split": case["split"],
                    "id": case["id"],
                    "score": score,
                    "max_score": max_score,
                    "passed_checks": ["check-1"] if score > 0 else [],
                    "failed_checks": [] if score == max_score else ["check-x"],
                    "notes": f"Scored {score}/{max_score}",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _prepare_ready_prompt_run(
        self,
        run_dir: Path,
        *,
        evaluator_mode: str = "subagent",
        subagent_system: str = "codex",
        panel_size: int = 1,
    ) -> None:
        create_run_scaffold(
            run_dir,
            InitOptions(
                goal="Improve the prompt.",
                artifact_type="prompt",
                evaluator_mode=evaluator_mode,
                subagent_system=subagent_system,
                panel_size=panel_size,
            ),
        )

        self._write_target_text(
            run_dir,
            "# Prompt\n\nFollow the rules exactly.\n",
        )
        (run_dir / "evals" / "checks.yaml").write_text(
            "\n".join(
                [
                    "checks:",
                    "  -",
                    "    id: format",
                    "    question: Does the response follow the requested format?",
                    "    pass: Required sections are present.",
                    "    fail: Required sections are missing.",
                    "  -",
                    "    id: constraints",
                    "    question: Does the response respect the prompt constraints?",
                    "    pass: No forbidden behavior appears.",
                    "    fail: A prompt rule is violated.",
                    "  -",
                    "    id: clarity",
                    "    question: Is the output concise and clear?",
                    "    pass: The answer is direct and easy to follow.",
                    "    fail: The answer is bloated or confusing.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (run_dir / "cases" / "train.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "train-1", "input": "a"}),
                    json.dumps({"id": "train-2", "input": "b"}),
                    json.dumps({"id": "train-3", "input": "c"}),
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (run_dir / "cases" / "holdout.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"id": "holdout-1", "input": "x"}),
                    json.dumps({"id": "holdout-2", "input": "y"}),
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _prepare_confirmed_prompt_run(
        self,
        run_dir: Path,
        *,
        evaluator_mode: str = "subagent",
        subagent_system: str = "codex",
        panel_size: int = 1,
    ) -> None:
        self._prepare_ready_prompt_run(
            run_dir,
            evaluator_mode=evaluator_mode,
            subagent_system=subagent_system,
            panel_size=panel_size,
        )
        draft_evals(run_dir)
        confirm_evals(run_dir)

    def test_create_document_copy_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "demo")
            spec = create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the copy.",
                    artifact_type="document-copy",
                ),
            )

            self.assertEqual(spec.run_id, "demo")
            self.assertEqual(spec.evaluation.evaluator_mode, "subagent")
            self.assertEqual(spec.evaluation.subagent_system, "codex")
            self.assertTrue(spec.evaluation.require_independent_evaluator)
            self.assertTrue((run_dir / "goal.md").exists())
            self.assertTrue((run_dir / "runbook.md").exists())
            self.assertTrue((run_dir / "spec.json").exists())
            self.assertTrue((run_dir / "spec.yaml").exists())
            self.assertTrue((run_dir / "state.json").exists())
            self.assertTrue(self._target_path(run_dir).exists())
            self.assertTrue((run_dir / "logs" / "experiments.tsv").exists())

            spec_text = (run_dir / "spec.yaml").read_text(encoding="utf-8")
            self.assertIn("artifact_type: document-copy", spec_text)
            self.assertIn("run_id: demo", spec_text)

    def test_copy_existing_prompt_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "prompt.md"
            source.write_text("# Prompt\n\nHello\n", encoding="utf-8")

            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "prompt-run")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                    artifact_path=str(source),
                ),
            )

            target_path = self._target_path(run_dir)
            self.assertEqual(target_path, source.resolve())
            self.assertEqual(target_path.read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))

    def test_next_reports_draft_before_cases_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "draft-run")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )

            payload = build_next_payload(run_dir)

            self.assertEqual(payload["phase"], "draft")
            self.assertEqual(payload["next_action"], "draft_evals")
            self.assertTrue(any(path.endswith("runbook.md") for path in payload["required_reads"]))

    def test_advance_runs_draft_evals_for_draft_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "advance-draft")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )

            payload = advance_run(run_dir)

            self.assertEqual(payload["phase_before"], "draft")
            self.assertTrue(payload["advanced"])
            self.assertEqual(payload["result"]["state_status"], "draft")
            self.assertTrue((run_dir / "reports" / "eval_draft.md").exists())

    def test_run_loop_stops_at_draft_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "loop-draft")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )

            payload = run_loop(run_dir)

            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["stop_reason"], "draft_work_required")
            self.assertEqual(payload["phase"], "draft")
            self.assertEqual(payload["transitions"], 1)
            self.assertEqual(payload["work_packet"]["work_type"], "draft_eval_setup")
            self.assertTrue(any(task["type"] == "checks" for task in payload["work_packet"]["tasks"]))

    def test_build_work_packet_for_draft_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "work-packet-draft")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )
            draft_evals(run_dir)

            payload = build_work_packet(run_dir)

            self.assertEqual(payload["phase"], "draft")
            self.assertEqual(payload["work_type"], "draft_eval_setup")
            self.assertTrue(any(path.endswith("checks.draft.yaml") for path in payload["suggestion_files"]))
            self.assertTrue(any(task["type"] == "train_cases" for task in payload["tasks"]))
            self.assertIn("Materialize the target, checks, judge prompt, and cases", payload["agent_prompt"])

    def test_next_reports_ready_when_cases_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "ready-run")
            self._prepare_confirmed_prompt_run(run_dir)

            readiness = assess_run_readiness(run_dir)
            self.assertTrue(readiness["ready_for_baseline"])

            payload = build_next_payload(run_dir)

            self.assertEqual(payload["phase"], "ready")
            self.assertEqual(payload["next_action"], "run_baseline")
            self.assertIn(str(self._target_path(run_dir)), payload["required_reads"])

    def test_advance_prepares_baseline_for_ready_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "advance-ready")
            self._prepare_confirmed_prompt_run(run_dir)

            payload = advance_run(run_dir)

            self.assertEqual(payload["phase_before"], "ready")
            self.assertTrue(payload["advanced"])
            self.assertEqual(payload["result"]["state_status"], "baseline_in_progress")
            self.assertTrue((run_dir / "outputs" / "exp-000" / "runner.md").exists())

    def test_run_loop_prepares_baseline_and_stops_for_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "loop-ready")
            self._prepare_confirmed_prompt_run(run_dir)

            payload = run_loop(run_dir)

            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["stop_reason"], "experiment_work_required")
            self.assertEqual(payload["phase"], "baseline_in_progress")
            self.assertEqual(payload["experiment"]["kind"], "baseline")
            self.assertEqual(payload["experiment"]["pending_cases"], 5)
            self.assertEqual(payload["work_packet"]["work_type"], "experiment_execution")
            self.assertEqual(payload["work_packet"]["pending_case_count"], 5)

    def test_next_reports_iterating_when_baseline_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "iterating-run")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )

            state_path = run_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["baseline_score"] = 0.58
            state["current_experiment"] = 1
            state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

            payload = build_next_payload(run_dir)

            self.assertEqual(payload["phase"], "iterating")
            self.assertEqual(payload["next_action"], "step")

    def test_draft_evals_waits_for_confirmation_when_requirements_are_met(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "draft-evals-ready")
            self._prepare_ready_prompt_run(run_dir)

            payload = draft_evals(run_dir)

            self.assertTrue(payload["ready_for_baseline"])
            self.assertEqual(payload["state_status"], "awaiting_confirmation")
            self.assertTrue((run_dir / "reports" / "eval_draft.md").exists())
            self.assertTrue((run_dir / "reports" / "eval_review.md").exists())
            self.assertTrue((run_dir / "reports" / "eval_review_prompt.md").exists())
            self.assertTrue((run_dir / "reports" / "eval_explained.md").exists())
            review_text = (run_dir / "reports" / "eval_review.md").read_text(encoding="utf-8")
            prompt_text = (run_dir / "reports" / "eval_review_prompt.md").read_text(encoding="utf-8")
            explained_text = (run_dir / "reports" / "eval_explained.md").read_text(encoding="utf-8")
            self.assertIn("## Evaluator Strategy", review_text)
            self.assertIn("built-in subagent", review_text)
            self.assertIn("host_system: `codex`", review_text)
            self.assertIn("explain the eval package in plain language", prompt_text)
            self.assertIn("evaluator strategy", prompt_text)
            self.assertIn("## What Baseline Means", explained_text)
            self.assertIn("## Why There Are Holdout Cases", explained_text)

    def test_confirm_evals_advances_to_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "confirm-evals")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = confirm_evals(run_dir, notes="User approved the eval package.")

            self.assertEqual(payload["state_status"], "ready")
            self.assertTrue(payload["eval_confirmed"])
            self.assertTrue((run_dir / "reports" / "eval_confirmation.md").exists())
            report_text = (run_dir / "reports" / "eval_confirmation.md").read_text(encoding="utf-8")
            self.assertIn("## Evaluator Strategy", report_text)
            self.assertIn("host_system: `codex`", report_text)

    def test_build_work_packet_for_confirmation_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "confirmation-work-packet")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = build_work_packet(run_dir)

            self.assertEqual(payload["phase"], "awaiting_confirmation")
            self.assertEqual(payload["work_type"], "confirmation_review")
            self.assertEqual(
                payload["summary"],
                "Review the drafted eval package and evaluator strategy with the user before starting baseline.",
            )
            self.assertTrue(any(path.endswith("reports/eval_review.md") for path in payload["review_files"]))
            self.assertTrue(any(path.endswith("reports/eval_explained.md") for path in payload["review_files"]))
            self.assertEqual(payload["evaluator_strategy"]["mode"], "subagent")
            self.assertEqual(payload["evaluator_strategy"]["host_system"], "codex")
            self.assertEqual(payload["evaluator_options"][0]["mode"], "subagent")
            self.assertIn("claude-code", payload["evaluator_options"][0]["examples"])
            self.assertIn("plain language", payload["agent_prompt"])
            self.assertTrue(
                any("Explain the eval package to the user in plain language" in step for step in payload["execution_steps"])
            )
            self.assertIn("start baseline", payload["agent_prompt"])
            self.assertIn("Do not silently default to `subagent` or `external_panel`", payload["agent_prompt"])
            self.assertIn("explicitly ask the user to choose it", "\n".join(payload["execution_steps"]))
            self.assertIn("external panel", "\n".join(payload["review_questions"]))
            self.assertTrue(
                any(command.startswith("digivolve confirm-evals") for command in payload["recommended_commands"])
            )

    def test_draft_evals_review_materials_can_describe_external_panel(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "draft-evals-external-panel")
            self._prepare_ready_prompt_run(run_dir, evaluator_mode="external_panel", panel_size=3)

            payload = draft_evals(run_dir)

            self.assertTrue(payload["ready_for_baseline"])
            review_text = (run_dir / "reports" / "eval_review.md").read_text(encoding="utf-8")
            self.assertIn("- mode: external panel", review_text)
            self.assertIn("- required_evaluators: `3`", review_text)
            self.assertIn("discuss with the user which external evaluators should be used", review_text)

    def test_configure_evaluators_updates_strategy_and_requires_reconfirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "configure-evaluators")
            self._prepare_confirmed_prompt_run(run_dir)

            payload = configure_evaluators(
                run_dir,
                mode="external_panel",
                panel_size=2,
                external_agents=["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"],
            )

            self.assertTrue(payload["configured"])
            self.assertEqual(payload["evaluation"]["mode"], "external_panel")
            self.assertEqual(payload["evaluation"]["panel_size"], 2)
            self.assertEqual(
                payload["evaluation"]["external_agents"],
                ["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"],
            )
            self.assertEqual(payload["state_status"], "awaiting_confirmation")
            self.assertFalse(payload["eval_confirmed"])
            self.assertEqual(payload["event"]["event_type"], "evaluator_strategy_configured")

            spec = json.loads((run_dir / "spec.json").read_text(encoding="utf-8"))
            self.assertEqual(spec["evaluation"]["evaluator_mode"], "external_panel")
            self.assertEqual(
                spec["evaluation"]["external_agents"],
                ["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"],
            )

    @mock.patch("agent_digivolve_harness.openrouter_eval._post_openrouter_request")
    def test_openrouter_panel_eval_records_external_verdicts(self, post_request: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "openrouter-panel")
            self._prepare_confirmed_prompt_run(run_dir, evaluator_mode="external_panel", panel_size=2)
            configure_evaluators(
                run_dir,
                mode="external_panel",
                panel_size=2,
                external_agents=["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"],
            )
            confirm_evals(run_dir, notes="User approved external evaluator panel.")
            prepare_baseline(run_dir)

            case_payload = load_case(run_dir, "train-1", split="train", experiment_id=0)
            output_path = run_dir / case_payload["case"]["case"]["output_file"]
            output_path.write_text("Evaluated output\n", encoding="utf-8")

            post_request.side_effect = [
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": True, "notes": "Format passes."})
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": True, "notes": "Constraints pass."})
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": False, "notes": "Clarity fails."})
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": True, "notes": "Format also passes."})
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": True, "notes": "Constraints also pass."})
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"passed": True, "notes": "Clarity passes here."})
                            }
                        }
                    ]
                },
            ]

            payload = openrouter_panel_eval(run_dir, "train-1", split="train", experiment_id=0)

            self.assertEqual(payload["record_count"], 6)
            self.assertTrue(payload["ready_to_finalize"])
            self.assertEqual(payload["models"], ["openai/gpt-5.4", "anthropic/claude-sonnet-4.5"])
            self.assertTrue(Path(payload["records"][0]["trace_path"]).exists())

            evals_payload = list_case_evaluations(run_dir, "train-1", split="train", experiment_id=0)
            self.assertEqual(evals_payload["recorded_verdicts"], 6)
            self.assertTrue(evals_payload["ready_to_finalize"])
            self.assertEqual(evals_payload["verdicts"][0]["evaluator"]["kind"], "openrouter")

            final_payload = finalize_case(run_dir, "train-1", split="train", experiment_id=0)
            self.assertTrue(final_payload["valid"])
            self.assertEqual(final_payload["aggregated_from"], 6)
            self.assertEqual(final_payload["result"]["score"], 2.0)

    def test_run_loop_stops_for_confirmation_before_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "loop-awaiting-confirmation")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = run_loop(run_dir)

            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["stop_reason"], "confirmation_required")
            self.assertEqual(payload["phase"], "awaiting_confirmation")
            self.assertEqual(payload["work_packet"]["work_type"], "confirmation_review")

    def test_draft_evals_writes_suggestion_files_when_run_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "draft-evals-suggestions")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the prompt.",
                    artifact_type="prompt",
                ),
            )

            payload = draft_evals(run_dir)

            self.assertFalse(payload["ready_for_baseline"])
            self.assertEqual(payload["state_status"], "draft")
            self.assertTrue((run_dir / "evals" / "checks.draft.yaml").exists())
            self.assertTrue((run_dir / "cases" / "train.draft.jsonl").exists())
            self.assertTrue((run_dir / "cases" / "holdout.draft.jsonl").exists())

            report_text = (run_dir / "reports" / "eval_draft.md").read_text(encoding="utf-8")
            self.assertIn("checks.draft.yaml", report_text)
            self.assertIn("train.draft.jsonl", report_text)

    def test_prepare_baseline_sets_in_progress_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "baseline-run")
            self._prepare_confirmed_prompt_run(run_dir)

            payload = prepare_baseline(run_dir)

            self.assertEqual(payload["state_status"], "baseline_in_progress")
            self.assertTrue((run_dir / "outputs" / "exp-000" / "manifest.json").exists())
            self.assertTrue((run_dir / "outputs" / "exp-000" / "runner.json").exists())
            self.assertTrue((run_dir / "outputs" / "exp-000" / "runner.md").exists())
            self.assertTrue((run_dir / "outputs" / "exp-000" / "cases" / "train-train-1.md").exists())
            self.assertTrue((run_dir / "outputs" / "exp-000" / "cases" / "train-train-1.json").exists())
            self.assertTrue((run_dir / "scores" / "exp-000" / "summary.json").exists())
            first_score = json.loads(
                (run_dir / "scores" / "exp-000" / "train-train-1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(first_score["max_score"], 3)
            self.assertEqual(first_score["available_checks"], ["format", "constraints", "clarity"])

            runner_payload = json.loads(
                (run_dir / "outputs" / "exp-000" / "runner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(runner_payload["adapter"], "prompt_runner")
            self.assertEqual(runner_payload["experiment_kind"], "baseline")
            self.assertTrue(runner_payload["checks_path"].endswith("evals/checks.yaml"))
            self.assertTrue(runner_payload["judge_path"].endswith("evals/judge.md"))
            self.assertTrue(runner_payload["summary_path"].endswith("scores/exp-000/summary.json"))
            self.assertTrue(runner_payload["case_contract"]["case_brief_dir"].endswith("outputs/exp-000/cases"))
            self.assertNotIn("candidate_dir", runner_payload["workspace"])
            self.assertIn("frozen_rules", runner_payload)

            next_payload = build_next_payload(run_dir)
            self.assertEqual(next_payload["phase"], "baseline_in_progress")
            self.assertEqual(next_payload["next_action"], "complete_baseline")
            self.assertTrue(any(path.endswith("outputs/exp-000/runner.md") for path in next_payload["required_reads"]))

    def test_prepare_baseline_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "baseline-needs-confirmation")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = prepare_baseline(run_dir)

            self.assertTrue(payload["confirmation_required"])
            self.assertEqual(payload["next_action"], "confirm_evals")
            self.assertEqual(payload["state_status"], "awaiting_confirmation")

    def test_finalize_baseline_advances_state_and_logs_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "baseline-finalize")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            summary_path = run_dir / "scores" / "exp-000" / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = finalize_baseline(run_dir)

            self.assertEqual(payload["state_status"], "baseline_complete")
            self.assertEqual(payload["next_action"], "step")

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "baseline_complete")
            self.assertEqual(state["baseline_score"], 70.0)

            experiments = (run_dir / "logs" / "experiments.tsv").read_text(encoding="utf-8")
            self.assertIn("\tbaseline\toriginal artifact", experiments)

    def test_advance_blocks_when_baseline_workspace_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "advance-baseline-blocked")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)

            payload = advance_run(run_dir)

            self.assertEqual(payload["phase_before"], "baseline_in_progress")
            self.assertFalse(payload["advanced"])
            self.assertTrue(payload["result"]["blocked"])
            self.assertEqual(payload["result"]["next_action"], "complete_baseline")

    def test_runner_and_case_commands_expose_active_case_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "execution-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)

            runner_payload = load_runner(run_dir)
            self.assertEqual(runner_payload["experiment_id"], 0)
            self.assertEqual(runner_payload["adapter"], "prompt_runner")
            self.assertTrue(any("Use the current committed target as-is" in step for step in runner_payload["execution_steps"]))
            self.assertIn("Follow these steps exactly:", runner_payload["agent_prompt"])

            cases_payload = list_cases(run_dir)
            self.assertEqual(cases_payload["total_cases"], 5)
            self.assertTrue(cases_payload["cases"][0]["bundle_file"].endswith(".json"))

            case_payload = load_case(run_dir, "train-1", split="train")
            self.assertEqual(case_payload["case"]["case"]["id"], "train-1")
            self.assertEqual(case_payload["case"]["case"]["split"], "train")
            self.assertEqual(case_payload["case"]["per_case_max_score"], 3)
            self.assertTrue(case_payload["case"]["bundle_path"].endswith("train-train-1.json"))
            self.assertIn("Do not mutate the artifact during this case", case_payload["case"]["agent_prompt"])
            self.assertIn("Do not self-score", case_payload["case"]["execution_steps"][-1])
            self.assertIn("isolated per check", case_payload["case"]["evaluator_prompt"])
            self.assertEqual(
                sorted(case_payload["case"]["evaluator_prompts"]),
                ["clarity", "constraints", "format"],
            )
            self.assertIn(
                "Independently evaluate only check `format`",
                case_payload["case"]["evaluator_prompts"]["format"],
            )

    def test_validate_case_reports_incomplete_and_complete_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "validate-case-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)

            before = validate_case(run_dir, "train-1", split="train")
            self.assertFalse(before["valid"])
            self.assertEqual(before["status"]["output"]["state"], "placeholder")

            manifest = json.loads(
                (run_dir / "outputs" / "exp-000" / "manifest.json").read_text(encoding="utf-8")
            )
            case = next(
                item for item in manifest["cases"] if item["split"] == "train" and item["id"] == "train-1"
            )
            self._write_case_files(run_dir, case, 3, 3)

            after = validate_case(run_dir, "train-1", split="train")
            self.assertTrue(after["valid"])
            self.assertEqual(after["result"]["score"], 3.0)
            self.assertEqual(after["status"]["score"]["state"], "ready")

    def test_record_eval_and_finalize_case_write_official_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "record-case-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)

            payload = self._record_independent_case_result(
                run_dir,
                case_id="train-1",
                split="train",
                output_text="Structured answer",
                score=2,
                notes="Missed one clarity condition.",
                passed_checks=["format", "constraints"],
                failed_checks=["clarity"],
            )

            self.assertTrue(payload["valid"])
            self.assertTrue(payload["recorded"])
            self.assertEqual(payload["result"]["score"], 2.0)
            self.assertEqual(payload["result"]["passed_checks"], ["format", "constraints"])
            self.assertEqual(payload["result"]["failed_checks"], ["clarity"])
            self.assertEqual(payload["aggregated_from"], 3)

    def test_record_eval_rejects_unknown_check_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "record-case-invalid")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            case_payload = load_case(run_dir, "train-1", split="train")
            output_path = run_dir / case_payload["case"]["case"]["output_file"]
            output_path.write_text("Structured answer\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unknown check referenced"):
                record_eval(
                    run_dir,
                    "train-1",
                    split="train",
                    evaluator_id="judge-1",
                    evaluator_kind="subagent",
                    check_id="missing-check",
                    passed=True,
                    notes="Bad check ids.",
                )

    def test_record_case_is_disallowed_when_independent_evaluators_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "record-case-blocked")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)

            with self.assertRaisesRegex(ValueError, "requires independent evaluator verdicts"):
                record_case(
                    run_dir,
                    "train-1",
                    split="train",
                    output_text="Structured answer",
                    score=2,
                    notes="Should be blocked.",
                    passed_checks=["format", "constraints"],
                    failed_checks=["clarity"],
                )

    def test_record_summary_writes_baseline_summary_from_case_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "record-summary-baseline")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            payload = record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )

            self.assertTrue(payload["recorded"])
            self.assertEqual(payload["kind"], "baseline")
            self.assertEqual(payload["summary"]["train_score"], 9.0)
            summary_path = run_dir / "scores" / "exp-000" / "summary.json"
            written = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(written["holdout_max_score"], 8.0)
            self.assertEqual(written["summary"], "Baseline is decent but inconsistent on structure.")

    def test_record_summary_requires_mutation_description_for_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "record-summary-step")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=1,
                train_score=10,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            with self.assertRaisesRegex(ValueError, "mutation_description is required"):
                record_summary(
                    run_dir,
                    experiment_id=1,
                    summary="Train improved while holdout held steady.",
                )

    def test_experiment_status_reports_pending_cases_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "experiment-status-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._record_independent_case_result(
                run_dir,
                case_id="train-1",
                split="train",
                output_text="Structured answer",
                score=2,
                notes="Missed one clarity condition.",
                passed_checks=["format", "constraints"],
                failed_checks=["clarity"],
            )

            payload = experiment_status(run_dir)

            self.assertEqual(payload["kind"], "baseline")
            self.assertEqual(payload["ready_cases"], 1)
            self.assertEqual(payload["pending_cases"], 4)
            self.assertFalse(payload["summary_ready"])
            self.assertFalse(payload["finalizable"])
            self.assertIn("train:train-2", payload["pending_case_ids"])

    def test_complete_experiment_finalizes_ready_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "complete-experiment-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )

            payload = complete_experiment(run_dir)

            self.assertTrue(payload["completed"])
            self.assertEqual(payload["kind"], "baseline")
            self.assertEqual(payload["result"]["state_status"], "baseline_complete")

    def test_build_work_packet_for_step_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "work-packet-step")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)

            payload = build_work_packet(run_dir)

            self.assertEqual(payload["phase"], "step_in_progress")
            self.assertEqual(payload["work_type"], "experiment_execution")
            self.assertTrue(payload["target_path"].endswith("worktrees/exp-001/target.md"))
            self.assertTrue(payload["worktree_path"].endswith("worktrees/exp-001"))
            self.assertEqual(payload["pending_case_count"], 5)
            self.assertTrue(payload["recommended_commands"][-1].startswith("digivolve complete-experiment"))
            self.assertTrue(any("candidate mutation" in step for step in payload["execution_steps"]))
            self.assertIn("Follow these steps exactly:", payload["pending_cases"][0]["agent_prompt"])
            self.assertTrue(payload["pending_cases"][0]["execution_steps"])
            self.assertIn("Pending cases:", payload["agent_prompt"])
            self.assertIn("Before recording case results, make exactly one mutation in the experiment worktree and commit it.", payload["agent_prompt"])

    def test_run_loop_finalizes_ready_baseline_and_prepares_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "loop-baseline-complete")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )

            payload = run_loop(run_dir)

            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["stop_reason"], "experiment_work_required")
            self.assertEqual(payload["phase"], "step_in_progress")
            self.assertEqual(payload["experiment"]["kind"], "step")
            self.assertEqual(payload["trace"][0]["phase_before"], "baseline_in_progress")
            self.assertEqual(payload["trace"][-1]["phase_after"], "step_in_progress")

    def test_run_loop_attaches_work_packet_when_step_finalize_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "loop-step-blocked")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)

            payload = run_loop(run_dir)

            self.assertTrue(payload["stopped"])
            self.assertEqual(payload["stop_reason"], "blocked")
            self.assertEqual(payload["phase"], "step_in_progress")
            self.assertEqual(payload["work_packet"]["work_type"], "experiment_execution")
            self.assertEqual(payload["work_packet"]["pending_case_count"], 5)

    def test_complete_experiment_blocks_when_no_experiment_is_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "complete-experiment-blocked")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            record_summary(
                run_dir,
                summary="Baseline is decent but inconsistent on structure.",
            )
            finalize_baseline(run_dir)

            payload = complete_experiment(run_dir)

            self.assertFalse(payload["completed"])
            self.assertEqual(payload["reason"], "no in-progress experiment to complete")

    def test_prepare_step_sets_step_in_progress_and_writes_candidate_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "step-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            summary_path = run_dir / "scores" / "exp-000" / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)

            payload = prepare_step(run_dir)

            self.assertEqual(payload["state_status"], "step_in_progress")
            self.assertEqual(payload["experiment_id"], 1)
            self.assertTrue((run_dir / "worktrees" / "exp-001").exists())
            self.assertTrue(self._active_target_path(run_dir).exists())
            self.assertTrue((run_dir / "outputs" / "exp-001" / "manifest.json").exists())
            self.assertTrue((run_dir / "outputs" / "exp-001" / "runner.json").exists())
            self.assertTrue((run_dir / "outputs" / "exp-001" / "runner.md").exists())
            self.assertTrue((run_dir / "outputs" / "exp-001" / "cases" / "train-train-1.md").exists())
            self.assertTrue((run_dir / "outputs" / "exp-001" / "cases" / "train-train-1.json").exists())

            runner_payload = json.loads(
                (run_dir / "outputs" / "exp-001" / "runner.json").read_text(encoding="utf-8")
            )
            self.assertEqual(runner_payload["adapter"], "prompt_runner")
            self.assertEqual(runner_payload["experiment_kind"], "step")
            self.assertTrue(runner_payload["artifact_path"].endswith("worktrees/exp-001/target.md"))
            self.assertTrue(runner_payload["workspace"]["worktree_path"].endswith("worktrees/exp-001"))
            self.assertTrue(runner_payload["workspace"]["target_path"].endswith("worktrees/exp-001/target.md"))

            next_payload = build_next_payload(run_dir)
            self.assertEqual(next_payload["phase"], "step_in_progress")
            self.assertEqual(next_payload["next_action"], "finalize_step")
            self.assertTrue(any(path.endswith("outputs/exp-001/runner.md") for path in next_payload["required_reads"]))
            self.assertIn(str(self._active_target_path(run_dir)), next_payload["required_reads"])

    def test_finalize_step_keeps_better_candidate_and_promotes_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "step-keep")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            baseline_summary = run_dir / "scores" / "exp-000" / "summary.json"
            baseline_summary.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)
            self._write_step_mutation(run_dir, "# Prompt\n\nFollow the rules exactly and stay concise.\n")
            self._write_case_results(
                run_dir,
                experiment_id=1,
                train_score=10,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            step_summary = run_dir / "scores" / "exp-001" / "summary.json"
            step_summary.write_text(
                json.dumps(
                    {
                        "mutation_description": "Added a concise instruction.",
                        "train_score": 10,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Train improved while holdout held steady.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = finalize_step(run_dir)

            self.assertEqual(payload["decision"], "keep")
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["best_candidate"], "exp-001")
            current_artifact = self._target_path(run_dir).read_text(encoding="utf-8")
            self.assertIn("stay concise", current_artifact)

    def test_finalize_step_discards_when_train_does_not_improve(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "step-discard")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            baseline_summary = run_dir / "scores" / "exp-000" / "summary.json"
            baseline_summary.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)
            self._write_step_mutation(run_dir, "# Prompt\n\nChanged wording but not the train result.\n")

            self._write_case_results(
                run_dir,
                experiment_id=1,
                train_score=9,
                train_max_score=12,
                holdout_score=6,
                holdout_max_score=8,
            )
            step_summary = run_dir / "scores" / "exp-001" / "summary.json"
            step_summary.write_text(
                json.dumps(
                    {
                        "mutation_description": "Changed wording.",
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 6,
                        "holdout_max_score": 8,
                        "summary": "Holdout improved but train stayed flat.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = finalize_step(run_dir)

            self.assertEqual(payload["decision"], "discard")
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["best_candidate"], "baseline")

    def test_generate_report_summarizes_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "report-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )

            baseline_summary = run_dir / "scores" / "exp-000" / "summary.json"
            baseline_summary.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)
            self._write_step_mutation(run_dir, "# Prompt\n\nFollow the rules exactly and stay concise.\n")
            self._write_case_results(
                run_dir,
                experiment_id=1,
                train_score=10,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            step_summary = run_dir / "scores" / "exp-001" / "summary.json"
            step_summary.write_text(
                json.dumps(
                    {
                        "mutation_description": "Added a concise instruction.",
                        "train_score": 10,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Train improved while holdout held steady.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_step(run_dir)

            payload = generate_report(run_dir)

            self.assertEqual(payload["state_status"], "iterating")
            self.assertEqual(payload["keep_count"], 1)
            self.assertEqual(payload["discard_count"], 0)
            self.assertEqual(payload["journal_entries"], 2)
            self.assertEqual(payload["best_candidate"], "exp-001")
            self.assertTrue((run_dir / "reports" / "run_report.md").exists())

            journal_entries = load_journal_entries(run_dir / "logs" / "journal.jsonl")
            self.assertEqual(len(journal_entries), 2)
            self.assertEqual(journal_entries[-1]["decision"], "keep")

    def test_resume_payload_summarizes_iterating_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "resume-run")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            baseline_summary = run_dir / "scores" / "exp-000" / "summary.json"
            baseline_summary.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)
            self._write_step_mutation(run_dir, "# Prompt\n\nFollow the rules exactly and stay concise.\n")
            self._write_case_results(
                run_dir,
                experiment_id=1,
                train_score=10,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            step_summary = run_dir / "scores" / "exp-001" / "summary.json"
            step_summary.write_text(
                json.dumps(
                    {
                        "mutation_description": "Added a concise instruction.",
                        "train_score": 10,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Train improved while holdout held steady.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_step(run_dir)

            payload = build_resume_payload(run_dir, limit=2)

            self.assertEqual(payload["phase"], "iterating")
            self.assertEqual(payload["next_action"], "step")
            self.assertEqual(len(payload["recent_journal_entries"]), 2)
            self.assertIn("run_report.md", payload["report_path"])
            self.assertTrue(payload["prioritized_reads"][0].endswith("run_report.md"))

    def test_resume_prioritizes_runner_brief_for_in_progress_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "resume-step")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._write_case_results(
                run_dir,
                experiment_id=0,
                train_score=9,
                train_max_score=12,
                holdout_score=5,
                holdout_max_score=8,
            )
            baseline_summary = run_dir / "scores" / "exp-000" / "summary.json"
            baseline_summary.write_text(
                json.dumps(
                    {
                        "train_score": 9,
                        "train_max_score": 12,
                        "holdout_score": 5,
                        "holdout_max_score": 8,
                        "summary": "Baseline is decent but inconsistent on structure.",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            finalize_baseline(run_dir)
            prepare_step(run_dir)

            payload = build_resume_payload(run_dir, limit=1)

            self.assertEqual(payload["phase"], "step_in_progress")
            self.assertTrue(payload["prioritized_reads"][1].endswith("active_step.json"))
            self.assertTrue(payload["prioritized_reads"][2].endswith("logs/events.jsonl"))
            self.assertTrue(payload["prioritized_reads"][3].endswith("outputs/exp-001/runner.md"))
            self.assertTrue(payload["prioritized_reads"][4].endswith("outputs/exp-001/manifest.json"))
            self.assertEqual(payload["current_workspace"]["type"], "step")
            self.assertTrue(payload["current_workspace"]["case_brief_dir"].endswith("outputs/exp-001/cases"))
            self.assertTrue(payload["current_workspace"]["worktree_path"].endswith("worktrees/exp-001"))
            self.assertTrue(payload["current_workspace"]["target_path"].endswith("worktrees/exp-001/target.md"))

    def test_add_run_note_records_event_without_changing_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "note-run")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = add_run_note(run_dir, "User clarified that brevity matters more than tone.")

            self.assertTrue(payload["recorded"])
            self.assertEqual(payload["event"]["event_type"], "user_note")
            self.assertEqual(payload["recent_events"][-1]["summary"], "User clarified that brevity matters more than tone.")

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "awaiting_confirmation")
            events = load_events(run_dir, limit=10)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "user_note")

    def test_interrupt_run_marks_active_step_and_pauses_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "interrupt-run")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)
            build_work_packet(run_dir)

            payload = interrupt_run(run_dir, "User stopped the current review to add more constraints.")

            self.assertEqual(payload["state_status"], "paused")
            self.assertEqual(payload["paused_from"], "awaiting_confirmation")
            self.assertEqual(payload["event"]["event_type"], "user_interrupt")
            self.assertEqual(payload["active_step"]["status"], "interrupted")

            active_step = json.loads((run_dir / "active_step.json").read_text(encoding="utf-8"))
            self.assertEqual(active_step["status"], "interrupted")
            self.assertEqual(active_step["phase"], "awaiting_confirmation")

            resume_payload = build_resume_payload(run_dir, limit=5)
            self.assertEqual(resume_payload["phase"], "paused")
            self.assertEqual(resume_payload["active_step"]["status"], "interrupted")
            self.assertEqual(resume_payload["recent_events"][-1]["event_type"], "user_interrupt")

    def test_change_direction_requires_replan_before_execution_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "change-direction-run")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)
            build_work_packet(run_dir)

            payload = change_run_direction(
                run_dir,
                "Baseline should optimize for policy adherence first, not just concise answers.",
            )

            self.assertEqual(payload["state_status"], "paused")
            self.assertTrue(payload["replan_required"])
            self.assertEqual(payload["active_step"]["status"], "stale")

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["replan_required"])
            self.assertEqual(state["status"], "paused")

            resume_payload = build_resume_payload(run_dir, activate=True)
            self.assertEqual(resume_payload["reactivate_to"], "replan_required")
            self.assertEqual(resume_payload["phase"], "replan_required")
            self.assertTrue(resume_payload["replan_required"])

            packet = build_work_packet(run_dir)
            self.assertEqual(packet["work_type"], "replan")
            self.assertEqual(packet["active_step"]["status"], "stale")
            self.assertEqual(packet["recent_events"][-1]["event_type"], "run_resumed")

    def test_resolve_replan_clears_flag_and_returns_fresh_work_packet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "resolve-replan")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)
            build_work_packet(run_dir)
            change_run_direction(
                run_dir,
                "The user now wants the eval package to emphasize escalation behavior.",
            )
            build_resume_payload(run_dir, activate=True)

            payload = resolve_replan(
                run_dir,
                summary="Updated the run direction to emphasize escalation and refreshed the review context.",
                next_step="Review the revised eval package with the user again.",
            )

            self.assertTrue(payload["recorded"])
            self.assertEqual(payload["phase"], "awaiting_confirmation")
            self.assertEqual(payload["event"]["event_type"], "replan_recorded")
            self.assertEqual(payload["work_packet"]["work_type"], "confirmation_review")

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertFalse(state["replan_required"])
            self.assertEqual(state["status"], "awaiting_confirmation")

            active_step = json.loads((run_dir / "active_step.json").read_text(encoding="utf-8"))
            self.assertEqual(active_step["status"], "in_progress")
            self.assertEqual(active_step["phase"], "awaiting_confirmation")

    def test_resume_can_reactivate_paused_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "paused-run")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)
            pause_run(run_dir)

            payload = build_resume_payload(run_dir, activate=True)

            self.assertEqual(payload["reactivate_to"], "awaiting_confirmation")
            self.assertTrue(payload["resumed"])

            state_path = run_dir / "state.json"
            updated_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_state["status"], "awaiting_confirmation")
            self.assertIsNone(updated_state["paused_from"])

    def test_pause_records_current_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "pause-run")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = pause_run(run_dir)

            self.assertEqual(payload["state_status"], "paused")
            self.assertEqual(payload["paused_from"], "awaiting_confirmation")

            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["paused_from"], "awaiting_confirmation")

    def test_status_summary_reports_waiting_for_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "status-confirmation")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)

            payload = build_status_summary(run_dir)

            self.assertEqual(payload["phase"], "awaiting_confirmation")
            self.assertEqual(payload["headline"], "Waiting For Approval Before Baseline")
            self.assertTrue(payload["waiting_for_user"])
            self.assertIn("waiting for your approval", payload["user_update"])
            self.assertEqual(payload["harness_next_action"], "confirm_evals")

    def test_status_summary_reports_experiment_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "status-experiment")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            self._record_independent_case_result(
                run_dir,
                case_id="train-1",
                split="train",
                output_text="Concise answer",
                score=1,
                notes="One case is recorded.",
                passed_checks=["format"],
                failed_checks=["constraints", "clarity"],
            )

            payload = build_status_summary(run_dir)

            self.assertEqual(payload["phase"], "baseline_in_progress")
            self.assertEqual(payload["headline"], "Experiment In Progress")
            self.assertEqual(payload["experiment_progress"]["kind"], "baseline")
            self.assertEqual(payload["experiment_progress"]["ready_cases"], 1)
            self.assertEqual(payload["experiment_progress"]["total_cases"], 5)
            self.assertIn("1/5 baseline cases are recorded", payload["progress_summary"])

    def test_case_evals_reports_recorded_independent_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "case-evals")
            self._prepare_confirmed_prompt_run(run_dir)
            prepare_baseline(run_dir)
            case_payload = load_case(run_dir, "train-1", split="train")
            output_path = run_dir / case_payload["case"]["case"]["output_file"]
            output_path.write_text("Structured answer\n", encoding="utf-8")
            record_eval(
                run_dir,
                "train-1",
                split="train",
                evaluator_id="judge-1",
                evaluator_kind="subagent",
                check_id="format",
                passed=True,
                notes="Missed one clarity condition.",
            )

            payload = list_case_evaluations(run_dir, "train-1", split="train")

            self.assertEqual(payload["recorded_verdicts"], 1)
            self.assertFalse(payload["ready_to_finalize"])
            self.assertTrue(payload["check_statuses"]["format"]["ready"])
            self.assertFalse(payload["check_statuses"]["constraints"]["ready"])
            self.assertEqual(payload["verdicts"][0]["evaluator"]["id"], "judge-1")

    def test_status_summary_surfaces_replan_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "status-replan")
            self._prepare_ready_prompt_run(run_dir)
            draft_evals(run_dir)
            build_work_packet(run_dir)
            change_run_direction(
                run_dir,
                "The user now wants the run to optimize escalation handling before brevity.",
            )

            payload = build_status_summary(run_dir)

            self.assertEqual(payload["phase"], "replan_required")
            self.assertEqual(payload["harness_phase"], "paused")
            self.assertEqual(payload["headline"], "Replanning Required")
            self.assertEqual(payload["latest_user_change"]["event_type"], "direction_change_requested")
            self.assertIn("replan before continuing", payload["user_update"])

    def test_document_copy_manifest_preserves_case_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = resolve_run_dir(Path(tmpdir) / "runs" / "doc-metadata")
            create_run_scaffold(
                run_dir,
                InitOptions(
                    goal="Improve the README.",
                    artifact_type="document-copy",
                ),
            )
            (run_dir / "cases" / "train.jsonl").write_text(
                json.dumps(
                    {
                        "id": "train-1",
                        "input": "Inspect the opening section.",
                        "kind": "section_case",
                        "selector": "opening",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_dir / "cases" / "holdout.jsonl").write_text(
                json.dumps(
                    {
                        "id": "holdout-1",
                        "input": "Answer an objection grounded in the README.",
                        "kind": "projection_case",
                        "projection": "objection_handling",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            manifest = baseline_module._build_manifest(run_dir)

            train_case = manifest["cases"][0]
            holdout_case = manifest["cases"][1]
            self.assertEqual(train_case["kind"], "section_case")
            self.assertEqual(train_case["selector"], "opening")
            self.assertEqual(holdout_case["kind"], "projection_case")
            self.assertEqual(holdout_case["projection"], "objection_handling")

    def test_document_copy_case_prompt_respects_case_kind(self) -> None:
        runner_payload = {
            "run_dir": "/tmp/run",
            "experiment_id": 0,
            "experiment_kind": "baseline",
            "adapter": "document_copy_runner",
            "artifact_type": "document-copy",
            "artifact_path": "/tmp/README.md",
            "repository_path": "/tmp",
            "checks_path": "/tmp/checks.yaml",
            "judge_path": "/tmp/judge.md",
            "checks": [],
            "summary_path": "/tmp/summary.json",
            "check_ids": ["quality"],
            "per_case_max_score": 1,
            "evaluation_units": [],
            "evaluation_contract": {
                "mode": "subagent",
                "panel_size": 1,
                "independent_required": True,
                "unit_isolation": "per_check",
            },
            "mutation_scope": {},
            "frozen_rules": [],
            "workspace": {
                "output_dir": "/tmp/out",
                "score_dir": "/tmp/score",
                "target_path": "/tmp/README.md",
            },
            "instructions": ["Read the README."],
        }

        section_case = build_case_payload(
            runner_payload,
            {
                "split": "train",
                "id": "train-1",
                "input": "Inspect the opening section.",
                "kind": "section_case",
                "selector": "opening",
                "output_file": "outputs/exp-000/train-train-1.md",
                "score_file": "scores/exp-000/train-train-1.json",
            },
        )
        projection_case = build_case_payload(
            runner_payload,
            {
                "split": "holdout",
                "id": "holdout-1",
                "input": "Answer the objection.",
                "kind": "projection_case",
                "projection": "objection_handling",
                "output_file": "outputs/exp-000/holdout-holdout-1.md",
                "score_file": "scores/exp-000/holdout-holdout-1.json",
            },
        )

        self.assertIn("Extract the relevant section", section_case["agent_prompt"])
        self.assertIn("selector `opening`", section_case["agent_prompt"])
        self.assertIn("Produce a derived answer grounded strictly in the README", projection_case["agent_prompt"])
        self.assertIn("projection `objection_handling`", projection_case["agent_prompt"])


if __name__ == "__main__":
    unittest.main()
