from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .yaml_utils import dump_yaml


DEFAULT_RUNS_ROOT = (Path(tempfile.gettempdir()) / "agent-digivolve-harness" / "runs").resolve()
DEFAULT_TARGETS_ROOT = (Path(tempfile.gettempdir()) / "agent-digivolve-harness" / "targets").resolve()
RUNS_ROOT_ENV_VAR = "AGENT_DIGIVOLVE_HARNESS_RUNS_ROOT"
TARGETS_ROOT_ENV_VAR = "AGENT_DIGIVOLVE_HARNESS_TARGETS_ROOT"
INIT_SENTINEL_SUFFIX = ".initializing"
RUN_INIT_WAIT_TIMEOUT_SECONDS = 5.0
RUN_INIT_POLL_INTERVAL_SECONDS = 0.01


def runs_root() -> Path:
    configured = os.environ.get(RUNS_ROOT_ENV_VAR)
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else DEFAULT_RUNS_ROOT
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def targets_root() -> Path:
    configured = os.environ.get(TARGETS_ROOT_ENV_VAR)
    root = (
        Path(configured).expanduser().resolve()
        if configured
        else DEFAULT_TARGETS_ROOT
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_run_dir(run_dir: Path | str) -> Path:
    raw = Path(run_dir).expanduser()
    canonical_root = runs_root()

    if raw.is_absolute():
        resolved = raw.resolve()
        if _is_relative_to(resolved, canonical_root):
            return resolved
    relative = _normalize_run_ref(raw)
    if not relative.parts:
        raise ValueError("run_dir must not be empty.")
    return (canonical_root / relative).resolve()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_run_state(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    wait_for_run_initialization(run_dir, required_files=("state.json",))
    return load_json(run_dir / "state.json")


def save_run_state(run_dir: Path, state: dict) -> None:
    run_dir = resolve_run_dir(run_dir)
    write_json(run_dir / "state.json", state)


def load_run_spec(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    wait_for_run_initialization(run_dir, required_files=("spec.json",))
    return load_json(run_dir / "spec.json")


def save_run_spec(run_dir: Path, spec: dict) -> None:
    run_dir = resolve_run_dir(run_dir)
    write_json(run_dir / "spec.json", spec)
    (run_dir / "spec.yaml").write_text(dump_yaml(spec) + "\n", encoding="utf-8")


def init_sentinel_path(run_dir: Path | str) -> Path:
    run_dir = resolve_run_dir(run_dir)
    return run_dir.parent / f".{run_dir.name}{INIT_SENTINEL_SUFFIX}"


def wait_for_run_initialization(
    run_dir: Path | str,
    *,
    required_files: tuple[str, ...] | list[str] = (),
    timeout_seconds: float = RUN_INIT_WAIT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = RUN_INIT_POLL_INTERVAL_SECONDS,
) -> Path:
    run_dir = resolve_run_dir(run_dir)
    required = tuple(required_files)
    sentinel = init_sentinel_path(run_dir)
    deadline = time.monotonic() + timeout_seconds

    while True:
        if _run_dir_has_required_files(run_dir, required):
            return run_dir
        if not sentinel.exists():
            return run_dir
        if time.monotonic() >= deadline:
            return run_dir
        time.sleep(poll_interval_seconds)


def _normalize_run_ref(raw: Path) -> Path:
    parts = list(raw.parts)
    if not parts:
        return Path()
    if "runs" in parts:
        last_runs = max(index for index, part in enumerate(parts) if part == "runs")
        tail = parts[last_runs + 1 :]
        return Path(*tail) if tail else Path()
    if raw.is_absolute():
        return Path(raw.name)
    return raw


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _run_dir_has_required_files(run_dir: Path, required_files: tuple[str, ...]) -> bool:
    if not run_dir.exists():
        return False
    if not required_files:
        return True
    return all((run_dir / item).exists() for item in required_files)
