# Releasing Agent Digivolve Harness

`Agent Digivolve Harness` has two release surfaces:

- the Python CLI package, published as `agent-digivolve-harness`
- the host-agent skill, published from `skills/agent-digivolve-harness-operator/`

They should be treated as related artifacts, but they are not the same thing.

## Intended Topology

- Publish the Python package to PyPI so `pip install agent-digivolve-harness` provides the `digivolve` command.
- Publish the repository to GitHub so host agents can install `skills/agent-digivolve-harness-operator/` via their skill installer.
- Let the skill bootstrap the CLI through `scripts/ensure_cli.sh`.

In other words:

- GitHub distributes the skill
- PyPI distributes the CLI

## Preflight Checklist

Before the first public release:

1. Choose and add a real open-source license file.
2. Publish the repository to GitHub.
3. Verify the final skill install path will be `skills/agent-digivolve-harness-operator/`.
4. Verify the package name on PyPI will be `agent-digivolve-harness`.
5. Confirm `src/agent_digivolve_harness/__init__.py` and `pyproject.toml` use the same version.
6. Run the test suite.
7. Build the wheel and sdist locally.

## Build And Upload The Package

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .[release] --no-build-isolation
python3 -m build
python3 -m twine check dist/*
python3 -m twine upload dist/*
```

If you prefer not to install the package editable into the release virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install build twine
python3 -m build
python3 -m twine check dist/*
python3 -m twine upload dist/*
```

## Installing The Skill After GitHub Publication

Once the repository is on GitHub, the Codex skill installer can pull the skill directly from the repo path:

```bash
python /Users/<user>/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo <owner>/<repo> \
  --path skills/agent-digivolve-harness-operator
```

After installing the skill, restart the host agent so it picks up new skills.

## How The Installed Skill Finds The CLI

The installed skill uses `skills/agent-digivolve-harness-operator/scripts/ensure_cli.sh`.

It resolves the CLI in this order:

1. `AGENT_DIGIVOLVE_HARNESS_CLI`
2. `digivolve` already on `PATH`
3. a skill-local virtual environment under `.runtime/`

When the skill has to install the CLI itself, it uses:

1. `AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT` for a local source tree
2. `AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC` for a wheel, VCS URL, or package spec
3. `agent-digivolve-harness` by default

Examples:

```bash
export AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC=agent-digivolve-harness
```

```bash
export AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC='git+https://github.com/<owner>/<repo>.git@main'
```

```bash
export AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT=/absolute/path/to/agent-digivolve-harness
```

## Suggested Release Order

For the cleanest first rollout:

1. Push the repository to GitHub.
2. Add the license file.
3. Build and publish `agent-digivolve-harness` to PyPI.
4. Install the skill from GitHub into a clean host environment.
5. Verify the skill can bootstrap the CLI without a local source checkout.
