from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .workspace import load_json, resolve_run_dir, write_json


EVENTS_FILE = Path("logs/events.jsonl")
ACTIVE_STEP_FILE = Path("active_step.json")


def ensure_coordination_files(run_dir: Path) -> None:
    run_dir = resolve_run_dir(run_dir)
    events_path = run_dir / EVENTS_FILE
    events_path.parent.mkdir(parents=True, exist_ok=True)
    if not events_path.exists():
        events_path.write_text("", encoding="utf-8")

    active_step_path = run_dir / ACTIVE_STEP_FILE
    if not active_step_path.exists():
        write_json(active_step_path, default_active_step())


def default_active_step() -> dict:
    return {
        "status": "idle",
        "phase": None,
        "work_type": None,
        "summary": None,
        "instructions": [],
        "recommended_commands": [],
        "done_when": None,
        "current_workspace": None,
        "source": "scaffold",
        "reason": None,
        "updated_at": _timestamp(),
    }


def load_active_step(run_dir: Path) -> dict:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    return load_json(run_dir / ACTIVE_STEP_FILE)


def save_active_step(run_dir: Path, payload: dict) -> dict:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    normalized = dict(payload)
    normalized["updated_at"] = _timestamp()
    write_json(run_dir / ACTIVE_STEP_FILE, normalized)
    return normalized


def reset_active_step(run_dir: Path, *, reason: str | None = None, source: str = "reset") -> dict:
    payload = default_active_step()
    payload["source"] = source
    payload["reason"] = reason
    return save_active_step(run_dir, payload)


def sync_active_step_from_packet(run_dir: Path, packet: dict) -> dict:
    status = "complete" if packet.get("phase") == "complete" else "in_progress"
    payload = {
        "status": status,
        "phase": packet.get("phase"),
        "work_type": packet.get("work_type"),
        "summary": packet.get("summary"),
        "instructions": list(packet.get("execution_steps", [])),
        "recommended_commands": list(packet.get("recommended_commands", [])),
        "done_when": packet.get("done_when"),
        "current_workspace": _workspace_from_packet(packet),
        "source": "work-packet",
        "reason": None,
    }
    return save_active_step(run_dir, payload)


def append_event(
    run_dir: Path,
    *,
    event_type: str,
    summary: str,
    details: dict | None = None,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    payload = {
        "timestamp": _timestamp(),
        "event_type": event_type,
        "summary": summary.strip(),
        "details": details or {},
    }
    path = run_dir / EVENTS_FILE
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    return payload


def load_events(run_dir: Path, *, limit: int | None = None) -> list[dict]:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    path = run_dir / EVENTS_FILE
    entries: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    if limit is None or limit < 0:
        return entries
    return entries[-limit:]


def active_step_path(run_dir: Path) -> str:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    return str((run_dir / ACTIVE_STEP_FILE).resolve())


def events_path(run_dir: Path) -> str:
    run_dir = resolve_run_dir(run_dir)
    ensure_coordination_files(run_dir)
    return str((run_dir / EVENTS_FILE).resolve())


def _workspace_from_packet(packet: dict) -> dict | None:
    keys = [
        "runner_path",
        "manifest_path",
        "summary_path",
        "target_path",
        "worktree_path",
        "adapter",
        "pending_case_count",
    ]
    workspace = {
        key: packet[key]
        for key in keys
        if packet.get(key) is not None
    }
    return workspace or None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
