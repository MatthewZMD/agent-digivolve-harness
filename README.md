# Agent Digivolve Harness

Agent Digivolve Harness is a control layer for long-running agent work. It is built around a simple observation: for many agent workflows, the first draft is not the hard part. The hard part is iteration.

A capable CLI agent can usually produce a reasonable first pass. The failure mode tends to appear later, when the work becomes iterative. After a few rounds, the evaluation target drifts, regressions get accepted because the newest draft sounds plausible, interruptions erase the external state that made earlier decisions intelligible, and the workflow starts to rely too heavily on self-judgment instead of a stable evaluation surface. In practice, many agent workflows degrade into unstructured trial and error long before the underlying model runs out of raw capability.

This project exists to externalize and stabilize that outer loop.

Instead of relying on conversational continuity alone, Agent Digivolve Harness gives the agent a persistent run directory, a fixed evaluation package, a user-calibrated rubric, labeled examples of good and bad outputs, a recorded baseline, bounded mutations, and explicit keep-or-revert decisions. The point is not simply to help an agent write something. The point is to help an agent improve something across multiple rounds without losing control of the process.

## The Core Idea

The underlying loop is simple:

1. Define what "better" means.
2. Lock a baseline.
3. Make one bounded change.
4. Evaluate again.
5. Keep the change or throw it away.
6. Repeat.

That structure is more important than any single command in this repository. The harness is useful because it turns repeated local experimentation into an explicit, inspectable process.

The run state lives on disk. The evaluation package is written down. The baseline is recorded. Each candidate is bounded. Each decision is explicit. That makes the loop more resilient to interruption and less dependent on whatever happens to still be in the current chat context.

## Why Not Just Ask An Agent To Rewrite Something?

A one-shot rewrite is often good enough when you only need a draft.

It is much less reliable when your real goal is controlled improvement across several rounds. The practical problems are familiar:

- the evaluation target drifts across iterations
- the newest version gets accepted without a disciplined comparison to baseline
- local improvements create regressions elsewhere
- interruptions leave no reliable external state to resume from
- the workflow gradually optimizes for plausible output rather than measured improvement

Agent Digivolve Harness is designed for that specific failure mode. It makes iteration a first-class object instead of treating it as a side effect of a long chat.

## What The Harness Actually Provides

At the implementation level, each run gives the agent:

- a persistent run directory on disk
- an explicit evaluation package
- a rubric for weighted user preferences and tradeoffs
- calibration examples that show what good and bad look like
- a baseline before mutation
- train and holdout cases
- one bounded mutation per iteration
- explicit keep, discard, pause, resume, and replan decisions

The agent remains the reasoning engine. `digivolve` is the control surface around the loop.

## Why This Is A Framework, Not Just A Tool

The important abstraction here is not "README optimization" or "prompt optimization" in isolation. It is the idea that if you can define an evaluation package for something, you can iterate on it in a disciplined way.

In practice, that package is not just checks and a judge prompt. This repository now scaffolds:

- `evals/checks.yaml` for binary gates
- `evals/judge.md` for stable evaluator instructions
- `evals/rubric.yaml` for weighted criteria, tradeoffs, and non-negotiables
- `evals/calibration.jsonl` for labeled good/bad examples with rationale

That is why the same outer loop can apply to different kinds of work. Today this repository ships three concrete adapters:

- `prompt`
- `document-copy`
- `repo-task`

Those are the currently implemented surfaces, not the conceptual limit of the pattern.

## Getting Started

### 1. Install The Python Package

For local development in this repository:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e . --no-build-isolation
```

If you want the packaged CLI instead:

```bash
python3 -m pip install agent-digivolve-harness
```

Then verify that the command is available:

```bash
digivolve --help
```

### 2. Install The Operator Skill

This repository includes an operator skill at `skills/agent-digivolve-harness-operator/`.

Install or expose that directory using your host agent's normal skill mechanism. The exact installation surface depends on the host, but the skill itself is intentionally host-agnostic.

When the skill needs to resolve a usable CLI, it does so in this order:

1. `AGENT_DIGIVOLVE_HARNESS_CLI`
2. `digivolve` already on `PATH`
3. `skills/agent-digivolve-harness-operator/scripts/ensure_cli.sh`

If bootstrap is required, `ensure_cli.sh` can install from:

1. `AGENT_DIGIVOLVE_HARNESS_SOURCE_ROOT`
2. `AGENT_DIGIVOLVE_HARNESS_PACKAGE_SPEC`
3. the published package name `agent-digivolve-harness`

### 3. Create A Run

Example:

```bash
digivolve init demo \
  --goal "Make this README easier for first-time GitHub readers to understand." \
  --artifact-type document-copy \
  --artifact-path ./README.md
```

### 4. Hand The Task To Your Agent

A natural handoff is:

```text
Use Agent Digivolve Harness to improve README.md. Create or continue the run named demo, explain the evaluation package to me in plain language before you ask me to approve it, then run the baseline and the first bounded iteration.
```

That can be Codex, Claude Code, OpenCode, or another CLI agent with file access and shell access.

## What The Operator Skill Enforces

The operator skill turns the harness from a CLI into an operating protocol. In practical terms, it tells the agent to:

- treat run state on disk as authoritative
- begin from the current harness contract rather than inventing a workflow
- get explicit user approval before confirming evals
- make exactly one bounded mutation per step
- reread the harness payload whenever the run state changes

The intended effect is straightforward: the user should not need to drive internal phase mechanics by hand, and the agent should not be improvising process decisions that the harness is meant to make explicit.

## Host Pattern

The current host pattern is also concrete:

- a CLI agent with file access
- shell access to run `digivolve`
- the ability to read the run artifacts that the skill points to

Examples include Codex, Claude Code, and OpenCode.

## Inspirations

The project is informed by three public references.

- [Karpathy's autoresearch](https://github.com/karpathy/autoresearch) influenced the emphasis on persistent external state and iterative improvement loops.
- [Anthropic's harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) informed the emphasis on generator-evaluator separation, structured handoff artifacts, and skeptical evaluation.
- [GEPA's optimize_anything](https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything/) informed the framing of one canonical artifact being evaluated across multiple cases with diagnostic feedback.

These references are inspirations for the direction of the harness. They are not claims that this repository implements those systems wholesale.

## Packaging

- project name: `agent-digivolve-harness`
- CLI command: `digivolve`
- Python module: `agent_digivolve_harness`
- operator skill: `skills/agent-digivolve-harness-operator/`

## Citation

```bibtex
@misc{agentdigivolveharness2026,
    title        = {Agent Digivolve Harness},
    author       = {{ProMed AI Team}},
    howpublished = {\url{https://github.com/MatthewZMD/agent-digivolve-harness}},
    year         = {2026},
    note         = {GitHub repository}
}
```
