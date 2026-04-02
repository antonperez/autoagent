"""
The agent-under-test. The meta-agent (Claude Code) iterates on this file.

Top: agent config (modify freely) + Harbor adapter (fixed harness).

Run all tasks:
  docker build -f Dockerfile.base -t autoagent-base .
  set -a && source .env && set +a
  uv run harbor run -p tasks/ --agent-import-path agent:AutoAgent -o jobs
"""

import asyncio, os, json
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ResultMessage, tool
from claude_agent_sdk.types import (
    AssistantMessage, UserMessage, TextBlock, ThinkingBlock,
    ToolUseBlock, ToolResultBlock,
)

# ===========================================================================
# AGENT CONFIG — meta-agent modifies this section
# ===========================================================================

SYSTEM_PROMPT = """You are a highly capable task-completion agent. You solve tasks by reading instructions, analyzing the problem, writing and executing code, and producing the required output files.

## Approach
1. Read /task/instruction.md to understand what's required.
2. Explore the working environment — check what files, tools, and libraries are available.
3. Plan your approach, then execute step by step.
4. Write output files to the exact paths specified in the instructions.
5. Verify your output before finishing.

## Key rules
- Use python3 (not python) for running scripts.
- Use Bash to run shell commands, install packages, inspect files.
- For data analysis: pandas, numpy, openpyxl are available.
- For file manipulation: use standard Python or shell tools.
- Always verify output files exist and contain valid content before finishing.
- If a task involves git repos, use git commands directly.
- If a task involves databases, use sqlite3 CLI or Python sqlite3 module.
- If a task involves images, use PIL/Pillow.
- Read error messages carefully and fix issues iteratively.
- Never give up — try multiple approaches if one fails.
"""

TOOLS_PRESET = {"type": "preset", "preset": "claude_code"}
CUSTOM_TOOLS = []
EXTERNAL_MCP_SERVERS = {}
SUBAGENTS = None
HOOKS = None

AGENT_CWD = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent")
SETTING_SOURCES = ["project"]

THINKING = {"type": "enabled", "budget_tokens": 10000}
EFFORT = None
OUTPUT_FORMAT = None
MODEL = "haiku"
FALLBACK_MODEL = None
MAX_TURNS = 30
MAX_BUDGET_USD = None
SANDBOX = None
ENABLE_FILE_CHECKPOINTING = False


def get_options() -> ClaudeAgentOptions:
    mcp = dict(EXTERNAL_MCP_SERVERS)
    if CUSTOM_TOOLS:
        from claude_agent_sdk import create_sdk_mcp_server
        mcp["tools"] = create_sdk_mcp_server("tools", tools=CUSTOM_TOOLS)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT, tools=TOOLS_PRESET, mcp_servers=mcp,
        cwd=AGENT_CWD,
        agents=SUBAGENTS, hooks=HOOKS, setting_sources=SETTING_SOURCES,
        thinking=THINKING, effort=EFFORT, output_format=OUTPUT_FORMAT,
        model=MODEL, fallback_model=FALLBACK_MODEL,
        max_turns=MAX_TURNS, max_budget_usd=MAX_BUDGET_USD,
        sandbox=SANDBOX, enable_file_checkpointing=ENABLE_FILE_CHECKPOINTING,
        permission_mode="bypassPermissions",
    )


# ===========================================================================
# HARBOR ADAPTER — fixed harness, do not modify
# ===========================================================================

from dotenv import dotenv_values
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class AutoAgent(BaseAgent):
    """Harbor agent adapter. Execs this file inside the container."""
    SUPPORTS_ATIF = True

    def __init__(self, *args, extra_env: dict[str, str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra_env = dict(extra_env) if extra_env else {}

    @staticmethod
    def name() -> str:
        return "autoagent"

    def version(self) -> str | None:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await environment.exec(command="mkdir -p /task")
        instr_file = self.logs_dir / "instruction.md"
        instr_file.write_text(instruction)
        await environment.upload_file(source_path=instr_file, target_path="/task/instruction.md")

        env = {"IS_SANDBOX": "1", **dotenv_values()}
        env = {k: v for k, v in env.items() if v}
        env.update(self._extra_env)

        result = await environment.exec(
            command="cd /app && python agent.py",
            env=env,
            timeout_sec=600,
        )
        if result.stdout:
            (self.logs_dir / "agent_stdout.txt").write_text(result.stdout)
        if result.stderr:
            (self.logs_dir / "agent_stderr.txt").write_text(result.stderr)

        traj_path = self.logs_dir / "trajectory.json"
        if traj_path.exists():
            try:
                fm = json.loads(traj_path.read_text()).get("final_metrics", {})
                context.cost_usd = fm.get("total_cost_usd")
                context.n_input_tokens = fm.get("total_prompt_tokens", 0)
                context.n_output_tokens = fm.get("total_completion_tokens", 0)
                context.n_cache_tokens = fm.get("total_cached_tokens", 0)
            except Exception:
                pass


# ===========================================================================
# CONTAINER ENTRYPOINT — fixed harness, do not modify
# ===========================================================================

def _trajectory_to_atif(messages: list, result_msg: ResultMessage | None) -> dict:
    """Convert SDK messages to ATIF trajectory dict."""
    steps, step_id = [], 0
    now = datetime.now(timezone.utc).isoformat()
    pending: dict[str, ToolUseBlock] = {}

    def _step(source, message, **kw):
        nonlocal step_id; step_id += 1
        s = {"step_id": step_id, "timestamp": now, "source": source, "message": message}
        s.update({k: v for k, v in kw.items() if v is not None})
        return s

    for msg in messages:
        if isinstance(msg, UserMessage):
            if isinstance(msg.content, list):
                all_tool_results = True
                for b in msg.content:
                    if isinstance(b, ToolResultBlock) and b.tool_use_id in pending:
                        tu = pending.pop(b.tool_use_id)
                        content = b.content if isinstance(b.content, str) else json.dumps(b.content) if b.content else ""
                        steps.append(_step("agent", f"Tool: {tu.name}",
                            tool_calls=[{"tool_call_id": tu.id, "function_name": tu.name, "arguments": tu.input}],
                            observation={"results": [{"source_call_id": tu.id, "content": content}]}))
                    else:
                        all_tool_results = False
                if all_tool_results:
                    continue
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
            if text:
                steps.append(_step("user", text))
        elif isinstance(msg, AssistantMessage):
            texts, reasoning = [], None
            for b in msg.content:
                if isinstance(b, TextBlock): texts.append(b.text)
                elif isinstance(b, ThinkingBlock): reasoning = b.thinking
                elif isinstance(b, ToolUseBlock): pending[b.id] = b
            if texts or reasoning:
                steps.append(_step("agent", "\n".join(texts) or "(thinking)",
                    reasoning_content=reasoning, model_name=msg.model))

    for tu in pending.values():
        steps.append(_step("agent", f"Tool: {tu.name}",
            tool_calls=[{"tool_call_id": tu.id, "function_name": tu.name, "arguments": tu.input}]))

    if not steps:
        steps.append(_step("user", "(empty)"))

    fm = None
    if result_msg:
        u = result_msg.usage or {}
        fm = {"total_prompt_tokens": u.get("input_tokens"), "total_completion_tokens": u.get("output_tokens"),
              "total_cached_tokens": u.get("cache_read_input_tokens"), "total_cost_usd": result_msg.total_cost_usd,
              "total_steps": len(steps), "extra": {"duration_ms": result_msg.duration_ms, "num_turns": result_msg.num_turns}}

    return {"schema_version": "ATIF-v1.2", "session_id": result_msg.session_id if result_msg else "unknown",
            "agent": {"name": "autoagent", "version": "0.1.0", "model_name": MODEL}, "steps": steps, "final_metrics": fm}


def _run_in_container():
    """Container entrypoint — reads instruction, runs agent, writes ATIF trajectory."""
    instruction = open("/task/instruction.md").read().strip()

    async def _run():
        opts = get_options()
        trajectory, result_msg = [], None
        async with ClaudeSDKClient(options=opts) as client:
            await client.query(instruction)
            async for msg in client.receive_response():
                trajectory.append(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg
        return trajectory, result_msg

    trajectory, result_msg = asyncio.run(_run())

    atif = _trajectory_to_atif(trajectory, result_msg)
    traj_dir = Path("/logs/agent")
    traj_dir.mkdir(parents=True, exist_ok=True)
    (traj_dir / "trajectory.json").write_text(json.dumps(atif, indent=2))

    if result_msg:
        print(f"cost_usd={result_msg.total_cost_usd or 0:.4f} turns={result_msg.num_turns} duration_ms={result_msg.duration_ms}")


if __name__ == "__main__":
    _run_in_container()
