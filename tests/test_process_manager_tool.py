"""Unit tests for process_manager tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from agents.tool_context import ToolContext

from strix.tools.process_manager.tools import (
    process_kill,
    process_list,
    process_output,
    process_write_stdin,
    shell_exec,
)


async def _call_shell(command: str, **kwargs: Any) -> str:
    ctx = ToolContext(context={}, tool_name="shell_exec", tool_call_id="c1", tool_arguments="{}")
    payload = {"command": command, **kwargs}
    return await shell_exec.on_invoke_tool(ctx, json.dumps(payload))


async def _call_list() -> str:
    ctx = ToolContext(context={}, tool_name="process_list", tool_call_id="c2", tool_arguments="{}")
    return await process_list.on_invoke_tool(ctx, json.dumps({}))


async def _call_output(task_id: str, **kwargs: Any) -> str:
    ctx = ToolContext(
        context={}, tool_name="process_output", tool_call_id="c3", tool_arguments="{}"
    )
    payload = {"task_id": task_id, **kwargs}
    return await process_output.on_invoke_tool(ctx, json.dumps(payload))


async def _call_kill(task_id: str) -> str:
    ctx = ToolContext(context={}, tool_name="process_kill", tool_call_id="c4", tool_arguments="{}")
    payload = {"task_id": task_id}
    return await process_kill.on_invoke_tool(ctx, json.dumps(payload))


async def _call_stdin(task_id: str, input_text: str) -> str:
    ctx = ToolContext(
        context={}, tool_name="process_write_stdin", tool_call_id="c5", tool_arguments="{}"
    )
    payload = {"task_id": task_id, "input_text": input_text}
    return await process_write_stdin.on_invoke_tool(ctx, json.dumps(payload))


def _extract_task_id(output: str) -> str:
    target = next(ln for ln in output.splitlines() if ln.startswith("Task ID:"))
    return target.split(":", 1)[1].strip()


@pytest.mark.asyncio
async def test_shell_exec_foreground_success() -> None:
    res = await _call_shell(command="echo 'STRIX_PROCESS_TEST'", background=False)
    assert "Exit Code: 0" in res
    assert "STRIX_PROCESS_TEST" in res


@pytest.mark.asyncio
async def test_shell_exec_foreground_timeout() -> None:
    res = await _call_shell(command="sleep 2", background=False, timeout_seconds=1)
    assert "Command Timed Out" in res


@pytest.mark.asyncio
async def test_shell_exec_background_lifecycle() -> None:
    # 1. Start background process
    start_res = await _call_shell(
        command="for i in 1 2 3 4 5; do echo step_$i; sleep 0.1; done",
        background=True,
        name="test_bg_job",
    )
    assert "Background Process Started" in start_res
    task_id = _extract_task_id(start_res)

    # 2. Check process_list
    list_res = await _call_list()
    assert task_id in list_res
    assert "test_bg_job" in list_res

    # Wait for completion
    await asyncio.sleep(0.7)

    # 3. Check process_output
    out_res = await _call_output(task_id=task_id, lines=20)
    assert "step_1" in out_res
    assert "step_5" in out_res


@pytest.mark.asyncio
async def test_process_kill() -> None:
    start_res = await _call_shell(
        command="sleep 30",
        background=True,
        name="long_sleeper",
    )
    task_id = _extract_task_id(start_res)

    kill_res = await _call_kill(task_id=task_id)
    assert "Successfully terminated" in kill_res or "had already exited" in kill_res

    list_res = await _call_list()
    assert "KILLED" in list_res or "COMPLETED" in list_res


@pytest.mark.asyncio
async def test_process_write_stdin() -> None:
    start_res = await _call_shell(
        command="cat",
        background=True,
        name="interactive_cat",
    )
    task_id = _extract_task_id(start_res)

    await asyncio.sleep(0.1)
    send_res = await _call_stdin(task_id=task_id, input_text="ping_cat_test\n")
    assert "Sent" in send_res

    await asyncio.sleep(0.2)
    out_res = await _call_output(task_id=task_id)
    assert "ping_cat_test" in out_res

    await _call_kill(task_id=task_id)
