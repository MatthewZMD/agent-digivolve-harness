from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class TargetRef:
    kind: str
    object_path: str
    repo_root: str
    repo_relpath: str


@dataclass(slots=True)
class MutationScope:
    mode: str = "file"
    allowed_sections: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunnerSpec:
    type: str = "codex_task"
    instruction_template: str | None = None


@dataclass(slots=True)
class GitState:
    baseline_commit: str | None = None
    current_commit: str | None = None
    best_commit: str | None = None
    active_worktree: str | None = None
    active_target_path: str | None = None
    active_parent_commit: str | None = None
    active_candidate_commit: str | None = None


@dataclass(slots=True)
class EvaluationSpec:
    checks_file: str = "evals/checks.yaml"
    judge_file: str = "evals/judge.md"
    holdout_required: bool = True
    require_confirmation: bool = True
    evaluator_mode: str = "subagent"
    subagent_system: str = "codex"
    require_independent_evaluator: bool = True
    panel_size: int = 1
    external_agents: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BudgetSpec:
    max_experiments: int = 20
    max_judge_calls: int = 200
    max_subagents: int = 2
    max_wall_clock_minutes: int = 60


@dataclass(slots=True)
class AcceptanceSpec:
    require_train_improvement: bool = True
    require_holdout_non_regression: bool = True
    discard_on_tie: bool = True


@dataclass(slots=True)
class RunSpec:
    run_id: str
    artifact_type: str
    goal: str
    target: TargetRef
    mutation_scope: MutationScope = field(default_factory=MutationScope)
    constraints: dict[str, list[str]] = field(
        default_factory=lambda: {"frozen_rules": []}
    )
    runner: RunnerSpec = field(default_factory=RunnerSpec)
    evaluation: EvaluationSpec = field(default_factory=EvaluationSpec)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    acceptance: AcceptanceSpec = field(default_factory=AcceptanceSpec)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class RunState:
    status: str = "draft"
    current_experiment: int = 0
    pending_experiment: int | None = None
    paused_from: str | None = None
    replan_required: bool = False
    replan_reason: str | None = None
    eval_drafted: bool = False
    eval_confirmed: bool = False
    best_candidate: str = "baseline"
    baseline_score: float | None = None
    best_score: float | None = None
    current_best_metrics: dict[str, float | str] | None = None
    git: GitState = field(default_factory=GitState)
    budget_used: dict[str, int] = field(
        default_factory=lambda: {
            "experiments": 0,
            "judge_calls": 0,
            "subagents": 0,
            "wall_clock_minutes": 0,
        }
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class InitOptions:
    goal: str
    artifact_type: str
    artifact_path: str | None = None
    run_id: str | None = None
    evaluator_mode: str = "subagent"
    subagent_system: str = "codex"
    panel_size: int = 1
    max_experiments: int = 20
    max_judge_calls: int = 200
    max_subagents: int = 2
    max_wall_clock_minutes: int = 60
