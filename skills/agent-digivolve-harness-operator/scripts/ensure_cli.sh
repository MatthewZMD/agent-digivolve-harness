#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${SKILL_DIR}/.runtime"
VENV_DIR="${RUNTIME_DIR}/agent-digivolve-harness-venv"
CLI_PATH="${VENV_DIR}/bin/digivolve"

if [[ -n "${AGENT_DIGIVOLVE_HARNESS_CLI:-}" ]]; then
  if [[ -x "${AGENT_DIGIVOLVE_HARNESS_CLI}" ]]; then
    printf '%s\n' "${AGENT_DIGIVOLVE_HARNESS_CLI}"
    exit 0
  fi
  printf 'AGENT_DIGIVOLVE_HARNESS_CLI is set but not executable: %s\n' "${AGENT_DIGIVOLVE_HARNESS_CLI}" >&2
  exit 1
fi

if command -v digivolve >/dev/null 2>&1; then
  command -v digivolve
  exit 0
fi

if [[ -x "${CLI_PATH}" ]]; then
  printf '%s\n' "${CLI_PATH}"
  exit 0
fi

mkdir -p "${RUNTIME_DIR}"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check --upgrade pip >/dev/null

if [[ -n "${AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT:-}" ]]; then
  if [[ ! -f "${AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT}/pyproject.toml" ]]; then
    printf 'AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT does not look like a Python project: %s\n' "${AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT}" >&2
    exit 1
  fi
  "${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check "${AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT}" >/dev/null
else
  PACKAGE_SPEC="${AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC:-agent-digivolve-harness}"
  "${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check "${PACKAGE_SPEC}" >/dev/null
fi

if [[ ! -x "${CLI_PATH}" ]]; then
  printf 'Failed to provision digivolve CLI at %s\n' "${CLI_PATH}" >&2
  exit 1
fi

printf '%s\n' "${CLI_PATH}"
