from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def append_journal_entry(
    path: Path,
    *,
    experiment_id: int,
    kind: str,
    decision: str,
    pass_rate: float,
    summary: str,
    details: dict,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "experiment_id": experiment_id,
        "kind": kind,
        "decision": decision,
        "pass_rate": pass_rate,
        "summary": summary,
        "details": details,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def load_journal_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries
