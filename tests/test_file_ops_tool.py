"""Unit tests for file_ops tools (read_file, write_file)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from agents.tool_context import ToolContext

from strix.tools.file_ops.tools import read_file, write_file


if TYPE_CHECKING:
    from pathlib import Path


async def _call_read(path: str, **kwargs: Any) -> str:
    ctx = ToolContext(context={}, tool_name="read_file", tool_call_id="c1", tool_arguments="{}")
    payload = {"path": path, **kwargs}
    return await read_file.on_invoke_tool(ctx, json.dumps(payload))


async def _call_write(path: str, content: str, mode: str = "overwrite") -> str:
    ctx = ToolContext(context={}, tool_name="write_file", tool_call_id="c2", tool_arguments="{}")
    payload = {"path": path, "content": content, "mode": mode}
    return await write_file.on_invoke_tool(ctx, json.dumps(payload))


@pytest.mark.asyncio
async def test_write_and_read_file(tmp_path: Path) -> None:
    target_file = tmp_path / "sub" / "test.txt"

    # Write file (creates parents automatically)
    write_res = await _call_write(
        path=str(target_file),
        content="line 1\nline 2\nline 3\n",
        mode="overwrite",
    )
    assert "Successfully wrote" in write_res
    assert target_file.exists()

    # Read file
    read_res = await _call_read(path=str(target_file))
    assert "line 1" in read_res
    assert "line 2" in read_res
    assert "line 3" in read_res
    assert "1 | line 1" in read_res


@pytest.mark.asyncio
async def test_read_file_line_slicing(tmp_path: Path) -> None:
    target_file = tmp_path / "slicing.txt"
    lines = [f"Item {i}" for i in range(1, 11)]
    target_file.write_text("\n".join(lines), encoding="utf-8")

    # Read lines 3 to 6
    sliced = await _call_read(path=str(target_file), start_line=3, end_line=6)
    assert "Lines 3-6 of 10" in sliced
    assert "Item 3" in sliced
    assert "Item 6" in sliced
    assert "Item 2" not in sliced
    assert "Item 7" not in sliced


@pytest.mark.asyncio
async def test_write_file_append_mode(tmp_path: Path) -> None:
    target_file = tmp_path / "append.txt"

    await _call_write(path=str(target_file), content="first\n", mode="overwrite")
    await _call_write(path=str(target_file), content="second\n", mode="append")

    read_res = await _call_read(path=str(target_file))
    assert "first" in read_res
    assert "second" in read_res


@pytest.mark.asyncio
async def test_read_file_not_found() -> None:
    res = await _call_read(path="/nonexistent_strix_path_987654")
    assert "Error: File not found" in res


@pytest.mark.asyncio
async def test_read_file_directory(tmp_path: Path) -> None:
    sub = tmp_path / "somedir"
    sub.mkdir()
    (sub / "a.txt").write_text("hello", encoding="utf-8")
    (sub / "b.py").write_text("print(1)", encoding="utf-8")

    res = await _call_read(path=str(sub))
    assert "Directory:" in res
    assert "a.txt" in res
    assert "b.py" in res


@pytest.mark.asyncio
async def test_read_file_binary(tmp_path: Path) -> None:
    bin_file = tmp_path / "data.bin"
    bin_file.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 50)

    res = await _call_read(path=str(bin_file))
    assert "Binary File:" in res
    assert "Binary content detected" in res
