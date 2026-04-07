# Testing Strategy

## Overview

AutoAgent does not have a traditional unit or integration test suite. Testing
is done by running the Harbor benchmark and observing the score. The agent
harness IS the product being tested; the benchmark IS the test suite.

## Running the Benchmark

### Build the base image (required before first run or after Dockerfile changes)

```bash
docker build -f Dockerfile.base -t autoagent-base .
```

### Run a single task

```bash
rm -rf jobs; mkdir -p jobs && \
uv run harbor run \
  -p tasks/ \
  --task-name "<task-name>" \
  -l 1 -n 1 \
  --agent-import-path agent:AutoAgent \
  -o jobs \
  --job-name latest \
  > run.log 2>&1
```

### Run all tasks (with parallelism)

```bash
rm -rf jobs; mkdir -p jobs && \
uv run harbor run \
  -p tasks/ \
  -n 100 \
  --agent-import-path agent:AutoAgent \
  -o jobs \
  --job-name latest \
  > run.log 2>&1
```

`-n` controls concurrency (default 4; 100 runs all tasks in parallel).

### Switch harness (Claude backend)

```bash
--agent-import-path agent-claude:AutoAgent
```

## Environment Setup

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Python dependencies
uv sync

# Set environment variables
cat > .env << 'EOF'
OPENAI_API_KEY=...    # for agent.py
# ANTHROPIC_API_KEY=...  # for agent-claude.py
EOF
```

The `.env` file is gitignored. `agent-claude.py` loads it via `dotenv_values()`
inside the Harbor adapter. `agent.py` relies on environment variables being set
in the shell.

## Interpreting Results

### run.log
Contains stdout/stderr from the harbor run. Check for crash diagnostics,
timeout errors, and per-task summaries.

### jobs/ directory
Each task gets a subdirectory under `jobs/latest/<task-name>/`. Contains:
- `trajectory.json` — ATIF-format agent conversation log
- `agent_stdout.txt` — agent process stdout (agent-claude.py)
- `agent_stderr.txt` — agent process stderr (agent-claude.py)
- Verifier output and reward score

### results.tsv
The meta-agent writes one row per experiment run:
```
commit  avg_score  passed  task_scores  cost_usd  status  description
```
- `passed`: e.g. `20/58`
- `status`: `keep`, `discard`, or `crash`

## Failure Analysis Workflow

1. Read `run.log` for infrastructure errors (container failures, timeouts).
2. Check `jobs/latest/<task-name>/trajectory.json` for agent behavior.
3. Check verifier logs in `jobs/latest/<task-name>/` for scoring details.
4. Group failures by root cause:
   - Misunderstood task instructions
   - Missing tool capability
   - Silent failure (agent thinks it succeeded but output is wrong)
   - Environment/dependency issue
   - Bad execution strategy
5. Fix the class of failures, not individual tasks.

## Keep / Discard Rules

- `passed` improved → keep
- `passed` same and harness is simpler → keep
- Otherwise → discard (but log learning in results.tsv and notes)

## Docker Cleanup (after many runs)

```bash
# Clean Harbor task image cache
uv run harbor cache clean -f

# Full Docker nuke (all unused images, build cache)
docker system prune -a -f

# Lighter: just dead containers
docker container prune -f
```

## Verifying Agent Import

Quick sanity check before a full run:

```bash
uv run python -c "from agent import AutoAgent; print('OK')"
uv run python -c "from agent_claude import AutoAgent; print('OK')"
```

## Task Format Reference

```
tasks/my-task/
  task.toml           -- config (timeouts, metadata)
  instruction.md      -- prompt sent to the agent
  tests/
    test.sh           -- entry point, writes /logs/reward.txt (score 0.0-1.0)
    test.py           -- verification logic
  environment/
    Dockerfile        -- task container (FROM autoagent-base)
  files/              -- reference files mounted into container
```

Tasks are gitignored and added per benchmark branch. The baseline run must
always be made before any harness changes to establish a reference score.
