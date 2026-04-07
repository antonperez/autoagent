# Codebase Conventions

## Code Quality Standards

### Single-File Harness Rule

Each harness is intentionally a single file (`agent.py`, `agent-claude.py`).
Do not split into modules. Simplicity is a first-class goal: fewer files,
less indirection, less special-case handling.

### Editable vs Fixed Sections

The two-section layout is enforced by comments in the harness files:

```
# ============================================================================
# EDITABLE HARNESS — prompt, tools, agent construction
# ============================================================================
```

and

```
# ============================================================================
# FIXED ADAPTER BOUNDARY: do not modify unless the human explicitly asks.
# ============================================================================
```

Never move code between sections without explicit human instruction. The
meta-agent only touches the editable section.

### Business Logic Placement

- Agent configuration (system prompt, model, limits) lives at module level as
  SCREAMING_SNAKE_CASE constants.
- Tool creation lives in `create_tools(environment)`.
- Agent construction lives in `create_agent(environment)`.
- Task orchestration lives in `run_task(environment, instruction)`.

### Tool Design

Tools are async functions decorated with `@function_tool` (openai-agents) or
`@tool` (claude_agent_sdk). Key principles:
- Tools should return structured data, not raw stdout where possible.
- Tool names should match the model's name-based priors (clear, descriptive
  names like `run_shell`, `read_file`, `write_file`).
- Prefer specialized tools over a single omnibus shell tool. A single
  `run_shell` forces the agent to write boilerplate from scratch each call.

### Error Handling

Tools catch exceptions and return error strings (never raise):

```python
try:
    result = await environment.exec(...)
    return result.stdout or "(no output)"
except Exception as exc:
    return f"ERROR: {exc}"
```

### Async Throughout

All agent code is async. Tools, agent run functions, and Harbor adapter methods
are all `async def`. Use `asyncio.run()` only at the container entrypoint
(`_run_in_container` in agent-claude.py).

## Naming Conventions

- **Constants**: `SCREAMING_SNAKE_CASE` — `SYSTEM_PROMPT`, `MODEL`,
  `MAX_TURNS`, `TOOLS_PRESET`, `AGENT_CWD`
- **Functions**: `snake_case` — `create_tools`, `create_agent`, `run_task`,
  `to_atif`, `_trajectory_to_atif`
- **Classes**: `PascalCase` — `AutoAgent`
- **Private helpers**: prefixed with `_` — `_step`, `_run_in_container`,
  `_trajectory_to_atif`
- **ATIF step helper**: inner function `_step()` defined inside serialization
  functions, uses `nonlocal step_id` counter

## File Organization

```
agent.py                 -- primary OpenAI-backend harness (meta-agent edits this)
agent-claude.py          -- Claude-backend harness (alternative entrypoint)
program.md               -- human-editable meta-agent directive
Dockerfile.base          -- DO NOT change model or Python version without human approval
pyproject.toml           -- add dependencies here when tools need new packages
```

## Common Patterns

### Adding a new tool (openai-agents)

```python
def create_tools(environment: BaseEnvironment) -> list[FunctionTool]:
    @function_tool
    async def my_tool(param: str) -> str:
        """Clear description the model can act on."""
        try:
            result = await environment.exec(command=f"some command {param}", timeout_sec=60)
            return result.stdout or "(no output)"
        except Exception as exc:
            return f"ERROR: {exc}"

    return [run_shell, my_tool]  # append new tool
```

### Adding a sub-agent (agent.as_tool)

```python
def create_agent(environment: BaseEnvironment) -> Agent:
    tools = create_tools(environment)
    verifier = Agent(
        name="verifier",
        instructions="Re-read the output and verify it matches requirements.",
        tools=tools,
        model=MODEL,
    )
    return Agent(
        name="autoagent",
        instructions=SYSTEM_PROMPT,
        tools=tools + [verifier.as_tool(...)],
        model=MODEL,
    )
```

### Modifying system prompt

Edit `SYSTEM_PROMPT` at the top of the editable section. Keep it focused and
avoid task-specific rules. The prompt should describe general task-solving
strategy, not hardcoded solutions to specific benchmark tasks.

## Anti-Patterns (DON'T)

- Do not add task-specific hacks or benchmark-keyword rules to the harness.
  The overfitting test: "If this exact task disappeared, would this still be
  a worthwhile harness improvement?" If no, skip it.
- Do not modify the FIXED ADAPTER section without explicit human instruction.
- Do not split the harness into multiple files.
- Do not run agent code directly on the host — always go through Docker via
  `uv run harbor run ...`.
- Do not add complexity that achieves the same `passed` count. Simpler wins.
- Do not hardcode model names from outside the editable section.
- Do not run `python` directly in shell — always use `python3` or `uv run`.
