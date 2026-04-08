"""Single-file Harbor agent harness: --agent-import-path agent:AutoAgent."""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone

import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from agents.usage import Usage

load_dotenv(dotenv_path=Path(__file__).parent / ".env")
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


# ============================================================================
# EDITABLE HARNESS — prompt, tools, agent construction
# ============================================================================

SYSTEM_PROMPT = """\
You are Andy, Anton's personal assistant.

You solve tasks by reading instructions carefully, writing files to the exact
paths specified, and confirming what you did in one concise sentence.

Rules:
- Write output files using: bash -c 'cat > /path/file << "EOF"\n...\nEOF'
- Use python3 for scripts (never bare python).
- Always verify output files exist before finishing.
- Be direct and brief. No filler phrases.

Email rules (no exceptions):
- ALWAYS include "Bcc: antonperez@me.com" on every outbound email draft.
- ALWAYS draft first, then ask Anton for confirmation before sending.
- Footer "Sent by Anton's personal AI": ask Anton per email whether to include it.

Scheduling rules:
- The "prompt" field in a schedule spec is what Anton sees when the reminder fires.
  It must state the day AND time explicitly (e.g. "Thursday at 9am — follow up with Sarah Chen.").

Memory rules:
- You have no memory of past conversations or previously filed data. If asked to recall something from a prior session, always say you don't have access to that information — never guess or fabricate.

Filing rules:
- If a filing request is ambiguous (content could belong to multiple folders, or the routing rules say "or ask"), ask the user to confirm the destination before filing. Do not file to multiple locations as a fallback.
- To ask for clarification: use bash to write your question to `/output/response.txt`. Do not just state the question in your text response — you must execute the bash write command.
"""

MODEL = "claude-sonnet-4-6"
MAX_TURNS = 30


class _Result:
    """Minimal stub satisfying the fixed adapter's to_atif() expectations."""
    new_items: list = []
    raw_responses: list = []
    last_response_id = None


async def run_task(
    environment: BaseEnvironment,
    instruction: str,
) -> tuple[object, int]:
    """Shell out to Claude Code CLI for sonnet parity via OAuth proxy."""
    _base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    t0 = time.time()

    # Write SYSTEM_PROMPT as CLAUDE.md so the CLI auto-loads it as its system prompt.
    encoded = base64.b64encode(SYSTEM_PROMPT.encode()).decode()
    await environment.exec(
        command=(
            f"python3 -c \"import base64; "
            f"open('/task/CLAUDE.md', 'w').write(base64.b64decode('{encoded}').decode())\""
        ),
        timeout_sec=15,
    )

    # Ensure all task-relevant dirs are writable by the non-root agent user.
    await environment.exec(
        command=(
            "mkdir -p /task /output /workspace/group && "
            "chown -R agent:agent /task /output /workspace 2>/dev/null; "
            "chown agent:agent /logs 2>/dev/null; true"
        ),
        timeout_sec=10,
    )

    # Run Claude Code CLI as non-root (required for --dangerously-skip-permissions).
    # The CLI handles tool use (Bash) natively, routing through the nanoclaw proxy.
    inner = (
        f"cd /task && "
        f"ANTHROPIC_BASE_URL={_base_url} "
        f"CLAUDE_CODE_OAUTH_TOKEN=placeholder "
        f"claude --model {MODEL} -p \"$(cat instruction.md)\" "
        f"--max-turns {MAX_TURNS} "
        f"--dangerously-skip-permissions"
    )
    cmd = f"su -s /bin/bash agent -c {repr(inner)} 2>&1"
    r = await environment.exec(command=cmd, timeout_sec=300)
    output = (r.stdout or "") + (r.stderr or "")
    if output.strip():
        print(output[:2000])  # log first 2k chars for debugging

    duration_ms = int((time.time() - t0) * 1000)
    return _Result(), duration_ms


# ============================================================================
# FIXED ADAPTER BOUNDARY: do not modify unless the human explicitly asks.
# Harbor integration and trajectory serialization live here.
# ============================================================================

def to_atif(result: object, model: str, duration_ms: int = 0) -> dict:
    """Convert OpenAI Agents SDK RunResult to an ATIF trajectory dict."""
    steps: list[dict] = []
    step_id = 0
    now = datetime.now(timezone.utc).isoformat()

    def _step(source: str, message: str, **extra: object) -> dict:
        nonlocal step_id
        step_id += 1
        step = {
            "step_id": step_id,
            "timestamp": now,
            "source": source,
            "message": message,
        }
        step.update({key: value for key, value in extra.items() if value is not None})
        return step

    pending_tool_call = None
    for item in result.new_items:
        if isinstance(item, MessageOutputItem):
            text = ItemHelpers.text_message_output(item)
            if text:
                steps.append(_step("agent", text, model_name=model))
        elif isinstance(item, ReasoningItem):
            summaries = getattr(item.raw_item, "summary", None)
            reasoning = "\n".join(s.text for s in summaries if hasattr(s, "text")) if summaries else None
            if reasoning:
                steps.append(
                    _step(
                        "agent",
                        "(thinking)",
                        reasoning_content=reasoning,
                        model_name=model,
                    )
                )
        elif isinstance(item, ToolCallItem):
            raw = item.raw_item
            if hasattr(raw, "name"):
                pending_tool_call = raw
        elif isinstance(item, ToolCallOutputItem) and pending_tool_call:
            arguments = (
                json.loads(pending_tool_call.arguments)
                if isinstance(pending_tool_call.arguments, str)
                else pending_tool_call.arguments
            )
            output_str = str(item.output) if item.output else ""
            steps.append(
                _step(
                    "agent",
                    f"Tool: {pending_tool_call.name}",
                    tool_calls=[
                        {
                            "tool_call_id": pending_tool_call.call_id,
                            "function_name": pending_tool_call.name,
                            "arguments": arguments,
                        }
                    ],
                    observation={
                        "results": [
                            {
                                "source_call_id": pending_tool_call.call_id,
                                "content": output_str,
                            }
                        ]
                    },
                )
            )
            pending_tool_call = None

    if pending_tool_call:
        arguments = (
            json.loads(pending_tool_call.arguments)
            if isinstance(pending_tool_call.arguments, str)
            else pending_tool_call.arguments
        )
        steps.append(
            _step(
                "agent",
                f"Tool: {pending_tool_call.name}",
                tool_calls=[
                    {
                        "tool_call_id": pending_tool_call.call_id,
                        "function_name": pending_tool_call.name,
                        "arguments": arguments,
                    }
                ],
            )
        )

    if not steps:
        steps.append(_step("user", "(empty)"))

    usage = Usage()
    for response in result.raw_responses:
        usage.add(response.usage)

    return {
        "schema_version": "ATIF-v1.6",
        "session_id": getattr(result, "last_response_id", None) or "unknown",
        "agent": {"name": "autoagent", "version": "0.1.0", "model_name": model},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": usage.input_tokens,
            "total_completion_tokens": usage.output_tokens,
            "total_cached_tokens": getattr(usage.input_tokens_details, "cached_tokens", 0) or 0,
            "total_cost_usd": None,
            "total_steps": len(steps),
            "extra": {"duration_ms": duration_ms, "num_turns": len(result.raw_responses)},
        },
    }


class AutoAgent(BaseAgent):
    """Harbor agent adapter. Runs the OpenAI agent host-side and proxies shell into the container."""

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

        result, duration_ms = await run_task(environment, instruction)

        atif = to_atif(result, model=MODEL, duration_ms=duration_ms)
        traj_path = self.logs_dir / "trajectory.json"
        traj_path.write_text(json.dumps(atif, indent=2))

        try:
            final_metrics = atif.get("final_metrics", {})
            context.n_input_tokens = final_metrics.get("total_prompt_tokens", 0)
            context.n_output_tokens = final_metrics.get("total_completion_tokens", 0)
            context.n_cache_tokens = final_metrics.get("total_cached_tokens", 0)
        except Exception:
            pass

        usage = Usage()
        for response in result.raw_responses:
            usage.add(response.usage)
        print(
            f"turns={len(result.raw_responses)} duration_ms={duration_ms} "
            f"input={usage.input_tokens} output={usage.output_tokens}"
        )


__all__ = ["AutoAgent"]
