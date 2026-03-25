from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .workspace import targets_root


DEFAULT_USER_NAME = "Agent Digivolve Harness"
DEFAULT_USER_EMAIL = "agent-digivolve-harness@example.invalid"


@dataclass(slots=True)
class GitTarget:
    kind: str
    object_path: str
    repo_root: str
    repo_relpath: str
    initial_commit: str


def bootstrap_target(
    *,
    run_id: str,
    artifact_type: str,
    artifact_path: str | None,
    placeholder_text: str,
) -> GitTarget:
    target_path = _resolve_target_path(run_id, artifact_type, artifact_path)
    target_kind = "directory" if artifact_type == "repo-task" else "file"
    repo_root = discover_repo_root(target_path if target_path.exists() else target_path.parent)
    if repo_root is not None:
        ensure_commit_identity(repo_root)
        if has_uncommitted_changes(repo_root):
            raise ValueError(f"Target repository must be clean before optimization starts: {repo_root}")

    if target_kind == "file":
        if target_path.exists() and target_path.is_dir():
            raise IsADirectoryError(f"Expected a file target, found directory: {target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if not target_path.exists():
            target_path.write_text(placeholder_text, encoding="utf-8")
    else:
        if target_path.exists() and not target_path.is_dir():
            raise NotADirectoryError(f"Expected a directory target, found file: {target_path}")
        target_path.mkdir(parents=True, exist_ok=True)

    if repo_root is None:
        repo_root = target_path if target_kind == "directory" else target_path.parent
        repo_root.mkdir(parents=True, exist_ok=True)
        _git(repo_root, "init")

    ensure_commit_identity(repo_root)

    repo_relpath = _repo_relpath(repo_root, target_path)
    head = current_commit(repo_root)
    if head is None or has_uncommitted_changes(repo_root):
        _stage_target(repo_root, target_path, target_kind)
        commit_message = (
            f"Bootstrap {artifact_type} target for run {run_id}"
            if target_path.exists()
            else f"Initialize target for run {run_id}"
        )
        _git(repo_root, "commit", "--allow-empty", "-m", commit_message)
        head = current_commit(repo_root)

    if head is None:
        raise ValueError(f"Failed to create an initial commit for target repository: {repo_root}")

    return GitTarget(
        kind=target_kind,
        object_path=str(target_path),
        repo_root=str(repo_root),
        repo_relpath=repo_relpath,
        initial_commit=head,
    )


def discover_repo_root(path: Path) -> Path | None:
    probe = path if path.is_dir() else path.parent
    result = _git(probe, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def ensure_commit_identity(repo_root: Path) -> None:
    name = _git(repo_root, "config", "--get", "user.name", check=False)
    if name.returncode != 0 or not name.stdout.strip():
        _git(repo_root, "config", "user.name", DEFAULT_USER_NAME)

    email = _git(repo_root, "config", "--get", "user.email", check=False)
    if email.returncode != 0 or not email.stdout.strip():
        _git(repo_root, "config", "user.email", DEFAULT_USER_EMAIL)


def has_uncommitted_changes(repo_root: Path) -> bool:
    result = _git(repo_root, "status", "--porcelain", check=True)
    return bool(result.stdout.strip())


def current_commit(repo_root: Path) -> str | None:
    result = _git(repo_root, "rev-parse", "HEAD", check=False)
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def create_experiment_worktree(repo_root: Path, worktree_path: Path, parent_commit: str) -> None:
    if worktree_path.exists():
        raise FileExistsError(f"Experiment worktree already exists: {worktree_path}")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "worktree", "add", "--detach", str(worktree_path), parent_commit)


def validate_candidate_commit(worktree_path: Path, parent_commit: str) -> str:
    if has_uncommitted_changes(worktree_path):
        raise ValueError(
            f"Experiment worktree must be fully committed before evaluation: {worktree_path}"
        )
    commit = current_commit(worktree_path)
    if commit is None:
        raise ValueError(f"Experiment worktree does not have a valid HEAD commit: {worktree_path}")
    if commit == parent_commit:
        raise ValueError(
            "The experiment worktree still points at the parent commit. Commit exactly one mutation before finalizing."
        )
    return commit


def cherry_pick_commit(repo_root: Path, commit: str) -> str:
    if has_uncommitted_changes(repo_root):
        raise ValueError(f"Target repository must be clean before promoting a candidate: {repo_root}")
    result = _git(repo_root, "cherry-pick", commit, check=False)
    if result.returncode != 0:
        _git(repo_root, "cherry-pick", "--abort", check=False)
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown cherry-pick failure"
        raise ValueError(f"Failed to promote candidate commit {commit}: {stderr}")
    promoted = current_commit(repo_root)
    if promoted is None:
        raise ValueError(f"Target repository HEAD is unavailable after promoting {commit}")
    return promoted


def remove_worktree(repo_root: Path, worktree_path: Path) -> None:
    if not worktree_path.exists():
        return
    _git(repo_root, "worktree", "remove", "--force", str(worktree_path), check=False)


def target_path_in_checkout(target: dict, checkout_root: str | Path) -> Path:
    root = Path(checkout_root).resolve()
    repo_relpath = target["repo_relpath"]
    if repo_relpath == ".":
        return root
    return (root / repo_relpath).resolve()


def ensure_target_commit_matches(repo_root: Path, expected_commit: str | None) -> None:
    if expected_commit is None:
        return
    actual = current_commit(repo_root)
    if actual != expected_commit:
        raise ValueError(
            f"Target repository HEAD drifted away from the run state. expected={expected_commit} actual={actual}"
        )


def _resolve_target_path(run_id: str, artifact_type: str, artifact_path: str | None) -> Path:
    if artifact_path:
        return Path(artifact_path).expanduser().resolve()

    root = targets_root() / run_id
    if artifact_type == "repo-task":
        return (root / "repo").resolve()
    suffix = ".md"
    return (root / f"target{suffix}").resolve()


def _repo_relpath(repo_root: Path, target_path: Path) -> str:
    relpath = target_path.resolve().relative_to(repo_root.resolve())
    return "." if str(relpath) == "." else str(relpath)


def _stage_target(repo_root: Path, target_path: Path, target_kind: str) -> None:
    if target_kind == "directory":
        _git(repo_root, "add", "--all", "--", ".")
        return
    relpath = _repo_relpath(repo_root, target_path)
    _git(repo_root, "add", "--", relpath)


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise ValueError(f"git {' '.join(args)} failed in {repo_root}: {stderr}")
    return completed
