from __future__ import annotations

from pathlib import Path


def load_checks(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []

    checks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("id:"):
            if current:
                checks.append(current)
            current = {"id": line.partition(":")[2].strip()}
        elif line.startswith("question:"):
            current["question"] = line.partition(":")[2].strip()
        elif line.startswith("pass:"):
            current["pass"] = line.partition(":")[2].strip()
        elif line.startswith("fail:"):
            current["fail"] = line.partition(":")[2].strip()
    if current:
        checks.append(current)

    return [
        check
        for check in checks
        if all(check.get(key) for key in ["id", "question", "pass", "fail"])
    ]


def load_check_ids(path: Path) -> list[str]:
    return [check["id"] for check in load_checks(path)]
