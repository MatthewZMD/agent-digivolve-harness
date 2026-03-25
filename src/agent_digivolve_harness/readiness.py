from __future__ import annotations

import json
from pathlib import Path

from .evaluation import load_checks
from .git_ops import current_commit, has_uncommitted_changes
from .workspace import load_run_spec


def assess_run_readiness(run_dir: Path) -> dict[str, object]:
    spec = load_run_spec(run_dir)
    artifact = _check_artifact(run_dir, spec)
    checks = _check_checks(run_dir / "evals" / "checks.yaml")
    judge = _check_judge(run_dir / "evals" / "judge.md")
    train = _check_cases(run_dir / "cases" / "train.jsonl", minimum=3)
    holdout = _check_cases(run_dir / "cases" / "holdout.jsonl", minimum=2)

    readiness = {
        "artifact": artifact,
        "checks": checks,
        "judge": judge,
        "train_cases": train,
        "holdout_cases": holdout,
    }
    readiness["ready_for_baseline"] = all(
        [
            artifact["ready"],
            checks["ready"],
            judge["ready"],
            train["ready"],
            holdout["ready"],
        ]
    )
    return readiness


def readiness_recommendations(readiness: dict[str, object]) -> list[str]:
    recommendations: list[str] = []

    artifact = readiness["artifact"]
    checks = readiness["checks"]
    judge = readiness["judge"]
    train = readiness["train_cases"]
    holdout = readiness["holdout_cases"]

    if not artifact["ready"]:
        recommendations.append("Replace the target placeholder with the real artifact content and commit it.")
    if not checks["ready"]:
        recommendations.append(
            "Refine `evals/checks.yaml` until it contains 3-5 complete binary checks."
        )
    if not judge["ready"]:
        recommendations.append("Refine `evals/judge.md` into a stable overall judge prompt.")
    if not train["ready"]:
        recommendations.append(
            "Populate `cases/train.jsonl` with at least 3 representative training cases."
        )
    if not holdout["ready"]:
        recommendations.append(
            "Populate `cases/holdout.jsonl` with at least 2 distinct holdout cases."
        )

    if not recommendations:
        recommendations.append("The run is ready for baseline.")

    return recommendations


def _check_artifact(run_dir: Path, spec: dict) -> dict:
    target = spec.get("target", {})
    target_path = target.get("object_path")
    repo_root = target.get("repo_root")

    if not target_path:
        return {"ready": False, "path": None, "reason": "missing target path"}

    path = Path(target_path).expanduser().resolve()
    if not path.exists():
        return {"ready": False, "path": str(path), "reason": "target path missing"}

    if repo_root:
        repo = Path(repo_root).expanduser().resolve()
        if has_uncommitted_changes(repo):
            return {"ready": False, "path": str(path), "reason": "target repository is dirty"}
        if current_commit(repo) is None:
            return {"ready": False, "path": str(path), "reason": "target repository has no commit"}

    if path.is_file():
        contents = path.read_text(encoding="utf-8").strip()
        if "<replace this file" in contents:
            return {"ready": False, "path": str(path), "reason": "placeholder target"}
        if contents == "":
            return {"ready": False, "path": str(path), "reason": "empty target"}

    return {"ready": True, "path": str(path), "reason": "target present"}


def _check_checks(path: Path) -> dict:
    if not path.exists():
        return {"ready": False, "count": 0, "reason": "missing checks file"}

    complete = load_checks(path)
    ready = 3 <= len(complete) <= 6
    reason = "ready" if ready else f"need 3-6 complete checks, found {len(complete)}"
    return {"ready": ready, "count": len(complete), "reason": reason}


def _check_judge(path: Path) -> dict:
    if not path.exists():
        return {"ready": False, "reason": "missing judge file"}

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ready = len(lines) >= 6
    reason = "ready" if ready else "judge prompt is too short"
    return {"ready": ready, "reason": reason}


def _check_cases(path: Path, minimum: int) -> dict:
    if not path.exists():
        return {"ready": False, "count": 0, "reason": "missing case file"}

    valid = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        case_id = payload.get("id")
        input_value = payload.get("input")
        if not case_id or not isinstance(input_value, str):
            continue
        if not input_value.strip() or "<replace" in input_value:
            continue
        valid += 1

    ready = valid >= minimum
    reason = "ready" if ready else f"need at least {minimum} valid cases, found {valid}"
    return {"ready": ready, "count": valid, "reason": reason}
