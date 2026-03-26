from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .evaluation import load_checks
from .execution import load_case, record_eval
from .workspace import load_run_spec, resolve_run_dir, write_json


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def openrouter_panel_eval(
    run_dir: Path,
    case_id: str,
    *,
    models: list[str] | None = None,
    experiment_id: int | None = None,
    split: str | None = None,
    timeout_seconds: int = 90,
    api_url: str = OPENROUTER_API_URL,
) -> dict:
    run_dir = resolve_run_dir(run_dir)
    spec = load_run_spec(run_dir)
    evaluation = spec.get("evaluation", {})
    if evaluation.get("evaluator_mode") != "external_panel":
        raise ValueError("OpenRouter panel evaluation requires `evaluation.evaluator_mode=external_panel`.")

    configured_models = [item for item in evaluation.get("external_agents", []) if item.strip()]
    requested_models = _normalize_models(models if models is not None else configured_models)
    if not requested_models:
        raise ValueError(
            "No OpenRouter evaluator models are configured. Use `configure-evaluators` or pass `--model`."
        )

    case_payload = load_case(run_dir, case_id, experiment_id=experiment_id, split=split)
    case_bundle = case_payload["case"]
    case = case_bundle["case"]
    output_path = run_dir / case["output_file"]
    if not output_path.exists():
        raise ValueError("Case output must be written before OpenRouter evaluation can start.")
    raw_output = output_path.read_text(encoding="utf-8").strip()
    if not raw_output:
        raise ValueError("Case output must be non-empty before OpenRouter evaluation can start.")

    checks = load_checks(run_dir / spec["evaluation"]["checks_file"])
    judge_prompt = (run_dir / spec["evaluation"]["judge_file"]).read_text(encoding="utf-8").strip()
    evaluation_units = case_bundle.get("evaluation_units") or checks

    records: list[dict[str, Any]] = []
    for index, model in enumerate(requested_models, start=1):
        evaluator_id = _evaluator_id(model, index)
        for unit in evaluation_units:
            request_body = {
                "model": model,
                "messages": _build_messages(
                    spec=spec,
                    case_bundle=case_bundle,
                    check=unit,
                    judge_prompt=judge_prompt,
                    raw_output=raw_output,
                ),
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "digivolve_check_evaluation",
                        "strict": True,
                        "schema": _response_schema(),
                    },
                },
            }
            response_payload = _post_openrouter_request(
                request_body,
                api_url=api_url,
                timeout_seconds=timeout_seconds,
            )
            verdict = _parse_verdict_response(response_payload)
            recorded = record_eval(
                run_dir,
                case_id,
                experiment_id=case_payload["experiment_id"],
                split=case["split"],
                evaluator_id=evaluator_id,
                evaluator_kind="openrouter",
                evaluator_label=model,
                model_name=model,
                check_id=unit["id"],
                passed=verdict["passed"],
                notes=verdict["notes"],
            )
            verdict_path = Path(recorded["verdict_path"])
            trace_path = verdict_path.with_suffix(".trace.json")
            write_json(
                trace_path,
                {
                    "model": model,
                    "check_id": unit["id"],
                    "request_body": request_body,
                    "response_payload": response_payload,
                    "parsed_verdict": verdict,
                },
            )
            records.append(
                {
                    "model": model,
                    "check_id": unit["id"],
                    "evaluator_id": evaluator_id,
                    "recorded": recorded,
                    "trace_path": str(trace_path.resolve()),
                }
            )

    ready_to_finalize = any(item["recorded"]["ready_to_finalize"] for item in records)
    return {
        "run_dir": str(run_dir),
        "experiment_id": case_payload["experiment_id"],
        "case_id": case["id"],
        "split": case["split"],
        "models": requested_models,
        "record_count": len(records),
        "records": records,
        "ready_to_finalize": ready_to_finalize,
        "next_action": "finalize_case" if ready_to_finalize else "openrouter_panel_eval",
    }


def _build_messages(
    *,
    spec: dict,
    case_bundle: dict,
    check: dict[str, str],
    judge_prompt: str,
    raw_output: str,
) -> list[dict[str, str]]:
    case = case_bundle["case"]
    rubric_text = str(case_bundle.get("rubric_text") or "").strip()
    calibration_summary = str(case_bundle.get("calibration_summary") or "").strip()
    rubric_block = (
        "User rubric:\n"
        "```text\n"
        f"{rubric_text}\n"
        "```\n\n"
        if rubric_text
        else ""
    )
    calibration_block = (
        "Calibration examples:\n"
        "```text\n"
        f"{calibration_summary}\n"
        "```\n\n"
        if calibration_summary
        else ""
    )
    user_prompt = (
        f"Goal:\n{spec['goal']}\n\n"
        f"Artifact type: {spec['artifact_type']}\n"
        f"Case split: {case['split']}\n"
        f"Case id: {case['id']}\n"
        f"Check under evaluation: {check['id']}\n\n"
        "Case input:\n"
        f"{case['input']}\n\n"
        "Raw output under evaluation:\n"
        "```text\n"
        f"{raw_output}\n"
        "```\n\n"
        "Check definition:\n"
        f"- id: {check['id']}\n"
        f"- question: {check['question']}\n"
        f"- pass: {check['pass']}\n"
        f"- fail: {check['fail']}\n\n"
        "Judge prompt:\n"
        "```text\n"
        f"{judge_prompt}\n"
        "```\n\n"
        f"{rubric_block}"
        f"{calibration_block}"
        "Evaluate only this check. Ignore all other possible checks.\n"
        "Return only JSON with this shape: {\"passed\": true|false, \"notes\": \"...\"}."
    )
    return [
        {
            "role": "system",
            "content": (
                "You are an independent evaluator. Do not improve the artifact or rewrite the raw output. "
                "Judge only and return JSON that matches the requested schema."
            ),
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]


def _response_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "passed": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        "required": ["passed", "notes"],
    }


def _post_openrouter_request(
    request_body: dict,
    *,
    api_url: str,
    timeout_seconds: int,
) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for OpenRouter evaluation.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if os.environ.get("OPENROUTER_SITE_URL"):
        headers["HTTP-Referer"] = os.environ["OPENROUTER_SITE_URL"]
    if os.environ.get("OPENROUTER_APP_NAME"):
        headers["X-OpenRouter-Title"] = os.environ["OPENROUTER_APP_NAME"]

    request = Request(
        api_url,
        data=json.dumps(request_body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ValueError(f"OpenRouter request failed with HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise ValueError(f"OpenRouter request failed: {exc.reason}") from exc


def _parse_verdict_response(response_payload: dict) -> dict:
    choices = response_payload.get("choices") or []
    if not choices:
        raise ValueError("OpenRouter response did not include any choices.")
    choice = choices[0]
    if choice.get("error"):
        raise ValueError(f"OpenRouter choice error: {choice['error']}")
    message = choice.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("OpenRouter response did not include text content.")

    payload = _parse_json_text(content)
    if not isinstance(payload, dict):
        raise ValueError("OpenRouter evaluator did not return a JSON object.")
    required = {"passed", "notes"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"OpenRouter evaluator JSON is missing fields: {', '.join(missing)}")

    return {
        "passed": bool(payload["passed"]),
        "notes": str(payload["notes"]).strip(),
    }


def _parse_json_text(content: str) -> Any:
    stripped = content.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            inner = "\n".join(lines[1:-1]).strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            return json.loads(inner)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(stripped[start : end + 1])
    raise ValueError("OpenRouter evaluator did not return parseable JSON.")


def _normalize_models(models: list[str] | None) -> list[str]:
    deduped: list[str] = []
    for item in models or []:
        normalized = item.strip()
        if normalized and normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _evaluator_id(model: str, index: int) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in model.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)
    return f"openrouter-{index}-{slug or 'model'}"
