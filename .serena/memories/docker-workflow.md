# Docker Workflow

## Container Architecture

AutoAgent uses Docker in two distinct roles:

1. **Base image** (`autoagent-base`): built from `Dockerfile.base`, contains
   the agent harness and Python dependencies. This is what task containers
   inherit from.
2. **Task containers**: created by Harbor per task, inheriting from
   `autoagent-base`. These are isolated sandboxes where agent code runs.

The host machine only runs Harbor orchestration (`uv run harbor run ...`).
Agent code runs inside Docker containers.

## Base Image

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git
WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system .
COPY agent.py ./
RUN ln -sf $(which python3) /usr/local/bin/python
RUN mkdir -p /logs /app/output
```

Key facts:
- Python 3.12 in the container (local dev uses 3.13 via `.python-version`)
- Dependencies installed system-wide via `uv pip install --system`
- Only `agent.py` and `pyproject.toml` are copied (harbor, docs, tasks,
  `.env`, `uv.lock` are excluded via `.dockerignore`)
- `/logs` and `/app/output` directories created for task output

## Common Commands

### Build base image

```bash
docker build -f Dockerfile.base -t autoagent-base .
```

Must be rebuilt after:
- Changes to `pyproject.toml` (new dependencies)
- Changes to `Dockerfile.base`
- Changes to `agent.py` (the file copied into the image)

### Run benchmark (all tasks)

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

### Run single task

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

### Inspect running containers

```bash
docker ps
docker logs <container-id>
```

### Clean up after runs

```bash
# Clean Harbor's cached task images and task cache (preferred first step)
uv run harbor cache clean -f

# Remove all unused images and build cache (aggressive)
docker system prune -a -f

# Only remove stopped containers
docker container prune -f
```

### If Docker becomes unresponsive (after many concurrent runs)

```bash
# macOS
killall Docker && open -a Docker

# Linux
sudo systemctl restart docker
```

## Environment Variables

The agent harness reads API keys from the environment. For `agent.py`:
- Set `OPENAI_API_KEY` in the shell or `.env` before running Harbor.

For `agent-claude.py`:
- `dotenv_values()` loads `.env` inside the Harbor adapter.
- `IS_SANDBOX=1` is automatically injected by the Harbor adapter.
- Variables are passed to the container via `environment.exec(..., env=env)`.

```bash
# Example .env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is gitignored and excluded from Docker images via `.dockerignore`.

## CRITICAL RULES

1. NEVER run agent code directly on the host. Always use `uv run harbor run`.
2. ALWAYS rebuild the base image after changing `agent.py` or `pyproject.toml`.
3. NEVER commit `.env` — it contains API keys.
4. ALWAYS clean up jobs (`rm -rf jobs`) before a new run to avoid stale results.
5. Tasks are gitignored — add them manually per benchmark branch.

## File Paths Inside the Container

```
/app/agent.py           -- harness code (copied from host)
/app/output/            -- general output directory
/task/instruction.md    -- task instruction (uploaded by Harbor adapter)
/logs/                  -- reward and verifier logs
/logs/agent/            -- ATIF trajectory JSON
/logs/reward.txt        -- score written by verifier (0.0-1.0)
```
