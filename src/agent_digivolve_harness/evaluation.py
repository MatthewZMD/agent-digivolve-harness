from __future__ import annotations

import json
from pathlib import Path

DEFAULT_RUBRIC_FILE = "evals/rubric.yaml"
DEFAULT_CALIBRATION_FILE = "evals/calibration.jsonl"
DESIGN_KEYWORDS = (
    "design",
    "frontend",
    "front-end",
    "landing page",
    "website",
    "web app",
    "ui",
    "ux",
    "dashboard",
    "visual",
    "brand",
    "typography",
    "layout",
    "css",
)


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


def rubric_file(spec: dict) -> str:
    return spec.get("evaluation", {}).get("rubric_file", DEFAULT_RUBRIC_FILE)


def calibration_file(spec: dict) -> str:
    return spec.get("evaluation", {}).get("calibration_file", DEFAULT_CALIBRATION_FILE)


def looks_design_oriented(goal: str) -> bool:
    lowered = goal.lower()
    return any(keyword in lowered for keyword in DESIGN_KEYWORDS)


def design_rubric_template() -> dict:
    return {
        "criteria": [
            {
                "id": "design_quality",
                "weight": 3,
                "priority": "must",
                "guidance": "Prefer work that feels like a coherent whole with a distinct mood and identity.",
            },
            {
                "id": "originality",
                "weight": 3,
                "priority": "must",
                "guidance": "Prefer deliberate custom decisions over template layouts, library defaults, or AI-generic patterns.",
            },
            {
                "id": "craft",
                "weight": 2,
                "priority": "should",
                "guidance": "Prefer strong hierarchy, spacing, color harmony, and contrast over broken fundamentals.",
            },
            {
                "id": "functionality",
                "weight": 2,
                "priority": "should",
                "guidance": "Prefer interfaces that users can understand and navigate without guessing.",
            },
        ],
        "non_negotiables": [
            "Do not ship obvious AI-generic layouts or untouched stock-component aesthetics.",
            "Do not accept broken typography hierarchy, spacing, contrast, or unusable flows.",
        ],
        "tradeoffs": [
            "Emphasize design quality and originality over superficial polish.",
            "If originality conflicts with usability, keep the interface understandable and usable.",
        ],
    }


def design_calibration_examples() -> list[dict[str, str]]:
    return [
        {
            "id": "good-1",
            "label": "good",
            "input": "Design a landing page for a museum.",
            "output": "A visually distinct experience with custom typography, deliberate layout, coherent mood, and clear navigation.",
            "why": "The design feels authored rather than assembled from defaults, while remaining usable.",
        },
        {
            "id": "bad-1",
            "label": "bad",
            "input": "Design a landing page for a museum.",
            "output": "A generic stack of white cards on a purple gradient with stock UI and no clear visual identity.",
            "why": "Template feel, weak originality, and clear signs of AI-generic design choices.",
        },
    ]


def load_support_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def load_calibration_examples(path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []

    examples: list[dict[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        example: dict[str, str] = {}
        for key in ("id", "label", "input", "output", "why"):
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                example[key] = text

        if any(example.get(key) for key in ("input", "output", "why")):
            examples.append(example)
        if limit is not None and len(examples) >= limit:
            break
    return examples


def format_calibration_examples(examples: list[dict[str, str]]) -> str:
    if not examples:
        return "- no calibration examples loaded"

    blocks: list[str] = []
    for index, example in enumerate(examples, start=1):
        blocks.append(f"Example {index}:")
        if example.get("id"):
            blocks.append(f"- id: `{example['id']}`")
        if example.get("label"):
            blocks.append(f"- label: `{example['label']}`")
        if example.get("input"):
            blocks.append(f"- input: {example['input']}")
        if example.get("output"):
            blocks.append(f"- output: {example['output']}")
        if example.get("why"):
            blocks.append(f"- why: {example['why']}")
    return "\n".join(blocks)


def excerpt_text(text: str, *, limit: int = 2000) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 3].rstrip() + "..."
