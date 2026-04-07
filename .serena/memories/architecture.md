# Project Architecture

## Project Type

AutoAgent is a meta-agent harness engineering system. It is not a web app or
service — it is a toolchain for autonomously improving an AI agent harness
through benchmark-driven hill-climbing. A "meta-agent" (typically Claude Code)
iterates on `agent.py` overnight, running Harbor benchmarks, reading scores,
and keeping or discarding changes based on whether they improve task pass rates.

## Tech Stack

- **Language**: Python 3.13 (pinned in `.python-version`)
- **Package manager**: uv (Astral)
- **Runtime**: Python 3.12 base in Docker (ghcr.io/astral-sh/uv:python3.12-bookworm-slim)
- **Agent SDKs**:
  - `openai-agents` — OpenAI Agents SDK used in `agent.py` (OpenAI-backend harness)
  - `claude_agent_sdk` — Claude Agent SDK used in `agent-claude.py` (Anthropic-backend harness)
- **Benchmark framework**: `harbor` — the Harbor evaluation framework (laude-institute/harbor)
- **Data libraries**: pandas, numpy, openpyxl (for spreadsheet/data tasks)
- **Container runtime**: Docker

## Directory Structure

```
autoagent/
  agent.py            -- OpenAI-backend single-file harness under test
  agent-claude.py     -- Claude-backend single-file harness under test
  Dockerfile.base     -- base Docker image (python3.12-bookworm-slim + uv)
  pyproject.toml      -- project metadata and dependencies
  program.md          -- meta-agent instructions + experiment directive (HUMAN edits this)
  .python-version     -- locks Python 3.13 for local dev (uv)
  .gitignore          -- excludes tasks/, jobs/, run.log, results.tsv, .env
  .dockerignore       -- excludes harbor, docs, tasks from the container image
  .agent/             -- optional agent workspace artifacts (created at runtime)
  tasks/              -- Harbor benchmark tasks (gitignored, added per branch)
  jobs/               -- Harbor job outputs (gitignored, runtime artifact)
  results.tsv         -- experiment ledger (gitignored, created by meta-agent)
  run.log             -- latest run output (gitignored)
```

## Key Architectural Patterns

### Pattern 1: Two-Section Single-File Harness

Both `agent.py` and `agent-claude.py` follow the same two-section layout:

- **EDITABLE section** (above the fixed adapter boundary): `SYSTEM_PROMPT`,
  `MODEL`, `MAX_TURNS`, `create_tools()`, `create_agent()`, `run_task()`.
  The meta-agent freely modifies everything in this section.
- **FIXED ADAPTER section** (below boundary): Harbor integration, ATIF
  trajectory serialization, the `AutoAgent(BaseAgent)` class. Do not touch
  this section unless the human explicitly asks.

### Pattern 2: Meta-Agent Hill-Climbing Loop

The experiment loop in `program.md`:
1. Read `run.log` and task-level results
2. Diagnose failures by root cause
3. Choose one general harness improvement
4. Edit `agent.py` (editable section only)
5. Commit, rebuild, rerun
6. Record to `results.tsv`
7. Keep if `passed` improved; discard otherwise

Primary metric: `passed` tasks. Secondary: `avg_score`. Simplicity breaks ties.

### Pattern 3: Harbor Agent Adapter

Both harnesses implement `AutoAgent(BaseAgent)` with `SUPPORTS_ATIF = True`.

`agent.py` (OpenAI): runs the agent host-side, proxies shell calls into the
container via `environment.exec()`.

`agent-claude.py` (Claude): copies itself into the container and runs as a
container entrypoint (`if __name__ == "__main__": _run_in_container()`),
using `ClaudeSDKClient` inside.

### Pattern 4: ATIF Trajectory Serialization

Both harnesses serialize agent trajectories to `trajectory.json` using the
ATIF (Agent Trajectory Interchange Format). Fields: `schema_version`,
`session_id`, `agent`, `steps`, `final_metrics`. Steps carry tool call/result
pairs with `tool_call_id` linking for pairing.

### Pattern 5: Docker Isolation

Tasks run inside Docker containers. The base image is built from
`Dockerfile.base`. The agent (especially `agent-claude.py`) copies the agent
file into `/app` and runs it inside the container. Task environments are
separate Harbor containers. The host never runs agent code directly; only
`uv run harbor run ...` is invoked on the host.

## External Integrations

- **Harbor** (`harbor` package): benchmark runner, task loader, environment
  management, ATIF output
- **OpenAI API**: used by `agent.py` via `openai-agents` SDK
- **Anthropic API**: used by `agent-claude.py` via `claude_agent_sdk`
- **Docker**: container execution environment for agent and tasks
