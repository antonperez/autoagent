# AutoAgent — Claude Code Project Instructions

## What This Project Is

AutoAgent is a meta-agent harness engineering system. The goal is to
autonomously improve an AI agent harness (`agent.py` or `agent-claude.py`)
by running Harbor benchmarks, reading scores, and hill-climbing on pass rate.

This is NOT a web app. There are no routes, controllers, or models. The
codebase is intentionally minimal: two single-file harnesses, a Dockerfile,
a pyproject.toml, and a meta-agent directive (`program.md`).

## Primary Files

| File | Role | Edit? |
|------|------|-------|
| `agent.py` | OpenAI-backend harness | Yes — editable section only |
| `agent-claude.py` | Claude-backend harness | Yes — editable section only |
| `program.md` | Meta-agent directive | Human only |
| `Dockerfile.base` | Base Docker image | Only if deps change |
| `pyproject.toml` | Dependencies | Only if new packages needed |

## Hard Rules

1. **Never touch the FIXED ADAPTER section** in `agent.py` or `agent-claude.py`
   without explicit human instruction. It is below the `FIXED ADAPTER BOUNDARY`
   comment.

2. **Never split the harness** into multiple files. Each harness is a
   single file by design.

3. **Never add task-specific hacks**. Apply the overfitting test: "If this
   exact task disappeared, would this still be a worthwhile improvement?"

4. **Simplicity wins** ties. Same pass count + simpler harness = keep.

5. **Never commit `.env`**. API keys stay out of the repository.

6. **Always rebuild** the base image after changing `agent.py` or
   `pyproject.toml`:
   ```bash
   docker build -f Dockerfile.base -t autoagent-base .
   ```

7. **Always clean jobs** before a new benchmark run:
   ```bash
   rm -rf jobs; mkdir -p jobs
   ```

## Benchmark Commands

```bash
# Run all tasks
uv run harbor run -p tasks/ -n 100 --agent-import-path agent:AutoAgent -o jobs --job-name latest > run.log 2>&1

# Run single task
uv run harbor run -p tasks/ --task-name "<task-name>" -l 1 -n 1 --agent-import-path agent:AutoAgent -o jobs --job-name latest > run.log 2>&1
```

## Experiment Loop

Read `program.md` for the full meta-agent directive. The loop is:
1. Baseline run
2. Read run.log + task trajectories
3. Diagnose failure patterns
4. Choose one general harness improvement
5. Edit editable section only
6. Commit + rebuild + rerun
7. Log to results.tsv
8. Keep or discard per metric rules

## Serena Memories

Load these at the start of every session:
- `.serena/memories/architecture.md` — project structure and patterns
- `.serena/memories/codebase-conventions.md` — naming and code rules
- `.serena/memories/testing-strategy.md` — how to run and interpret benchmarks
- `.serena/memories/docker-workflow.md` — Docker commands and container layout

## Constitution

`.claude/settings/constitution.json` defines architectural rules. Check it
before any harness change. Key principles:
- `fixed_adapter_immutability` (critical)
- `no_overfitting` (critical)
- `single_file_harness` (critical)
- `simplicity_wins` (high)
- `metric_driven_decisions` (high)
