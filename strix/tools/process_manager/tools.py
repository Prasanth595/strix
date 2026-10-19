"""Process Manager tools — Foreground & background multi-run shell execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents import RunContextWrapper, function_tool

from strix.runtime.session_manager import session_workspace_root


logger = logging.getLogger(__name__)

_LOG_DIR = Path(tempfile.gettempdir()) / "strix_tasks"
_LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TaskRecord:
    task_id: str
    command: str
    name: str
    pid: int | None
    started_at: str
    start_time: float
    proc: asyncio.subprocess.Process | None
    log_file: Path
    status: str
    exit_code: int | None = None
    end_time: float | None = None


_TASKS: dict[str, TaskRecord] = {}
_TASK_COUNTER = 0
_REGISTRY_LOCK = asyncio.Lock()
_WATCHER_TASKS: set[asyncio.Task[None]] = set()


def _resolve_workdir(workdir: str | None, ctx: RunContextWrapper | None = None) -> Path:
    """Resolve working directory for command execution."""
    if workdir and workdir.strip():
        p_str = workdir.strip()
        if p_str.startswith("~"):
            return Path(p_str).expanduser().resolve()
        return Path(p_str).resolve()

    workspace = None
    if ctx is not None and isinstance(ctx.context, dict):
        session = ctx.context.get("sandbox_session")
        if session is not None:
            with contextlib.suppress(Exception):
                root_str = session_workspace_root(session)
                if root_str:
                    workspace = Path(root_str).resolve()

    if workspace is not None and workspace.exists():
        return workspace

    return Path.cwd().resolve()


async def _watch_process(record: TaskRecord, file_handle: Any) -> None:
    """Background task watcher that settles exit status and cleans up file handles."""
    try:
        if record.proc is not None:
            code = await record.proc.wait()
            record.exit_code = code
            record.end_time = time.perf_counter()
            if record.status == "RUNNING":
                record.status = "COMPLETED" if code == 0 else "FAILED"
    except Exception:
        logger.exception("Error watching process %s", record.task_id)
    finally:
        with contextlib.suppress(Exception):
            file_handle.close()


def _format_duration(duration_s: float) -> str:
    """Format duration in human-readable seconds or minutes."""
    if duration_s < 60:
        return f"{int(duration_s)}s"
    minutes = int(duration_s // 60)
    seconds = int(duration_s % 60)
    return f"{minutes}m {seconds}s"


async def _run_foreground(command: str, resolved_cwd: Path, timeout_s: int) -> str:
    """Run a command synchronously in the foreground."""
    start_time = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(resolved_cwd),
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_s,
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return (
                f"[Command Timed Out after {timeout_s}s]\n"
                f"Command: {command}\n"
                f"Working Dir: {resolved_cwd}\n"
                "Tip: Run long-running scans, listeners, or servers with background=True."
            )

        duration = time.perf_counter() - start_time
        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = proc.returncode

        output_lines = [
            f"[Command: {command}]",
            f"[Exit Code: {exit_code} | Duration: {duration:.2f}s | Cwd: {resolved_cwd}]",
        ]

        if stdout_text:
            output_lines.append(f"--- Stdout ---\n{stdout_text}")
        if stderr_text:
            output_lines.append(f"--- Stderr ---\n{stderr_text}")
        if not stdout_text and not stderr_text:
            output_lines.append("(No output produced)")

        return "\n".join(output_lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error executing command: {exc}"


async def _run_background(command: str, resolved_cwd: Path, name: str | None) -> str:
    """Launch a command asynchronously in the background."""
    global _TASK_COUNTER  # noqa: PLW0603

    async with _REGISTRY_LOCK:
        _TASK_COUNTER += 1
        task_id = f"task_{_TASK_COUNTER}"
        task_name = name.strip() if name and name.strip() else f"cmd_{_TASK_COUNTER}"
        log_path = _LOG_DIR / f"{task_id}.log"

        try:
            log_file = log_path.open("w", encoding="utf-8", buffering=1)
        except OSError as exc:
            return f"Error creating log file: {exc}"

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(resolved_cwd),
            )
        except Exception as exc:  # noqa: BLE001
            log_file.close()
            return f"Error launching background process: {exc}"

        now_str = datetime.now(UTC).strftime("%H:%M:%S")
        record = TaskRecord(
            task_id=task_id,
            command=command,
            name=task_name,
            pid=proc.pid,
            started_at=now_str,
            start_time=time.perf_counter(),
            proc=proc,
            log_file=log_path,
            status="RUNNING",
        )
        _TASKS[task_id] = record

        watcher_task = asyncio.create_task(_watch_process(record, log_file))
        _WATCHER_TASKS.add(watcher_task)
        watcher_task.add_done_callback(_WATCHER_TASKS.discard)

        return (
            f"✅ [Background Process Started]\n"
            f"Task ID: {task_id}\n"
            f"Name: {task_name}\n"
            f"PID: {proc.pid}\n"
            f"Status: RUNNING\n"
            f"Log File: {log_path}\n"
            f"Command: {command}\n\n"
            f"Management Tools:\n"
            f"- process_list() -> View all active/finished background jobs\n"
            f"- process_output(task_id='{task_id}') -> Read latest stdout/stderr logs\n"
            f"- process_write_stdin(task_id='{task_id}', input_text='...') -> Send input\n"
            f"- process_kill(task_id='{task_id}') -> Stop the process"
        )


@function_tool(timeout=660)
async def shell_exec(
    ctx: RunContextWrapper,
    command: str,
    background: bool = False,
    timeout_seconds: int = 60,
    name: str | None = None,
    workdir: str | None = None,
) -> str:
    """Execute a shell command in foreground or background mode.

    Modes:
    1. Foreground (background=False, default):
       Runs the command and waits for completion (up to timeout_seconds).
       Returns stdout, stderr, exit code, and execution time.

    2. Background (background=True):
       Immediately launches long-running tasks asynchronously
       (e.g., nmap -p-, nc -lvnp listeners, web servers, brute-forcers, tunnels).
       Output is streamed to a dedicated log file. Returns task_id for tracking.

    Args:
        command: The shell command string to execute.
        background: True to run asynchronously in background; False to wait.
        timeout_seconds: Wall-clock timeout in seconds for foreground commands (max 600).
        name: Optional friendly name for the task (e.g. "nmap_all_ports", "nc_listener_4444").
        workdir: Working directory for execution (defaults to workspace or current directory).
    """
    if not command or not command.strip():
        return "Error: Command cannot be empty."

    resolved_cwd = _resolve_workdir(workdir, ctx)
    if not resolved_cwd.exists():
        resolved_cwd = Path.cwd().resolve()

    timeout_s = max(1, min(600, timeout_seconds))

    if not background:
        return await _run_foreground(command, resolved_cwd, timeout_s)

    return await _run_background(command, resolved_cwd, name)


@function_tool(timeout=30)
async def process_list(ctx: RunContextWrapper) -> str:
    """List all background processes and their current status.

    Shows Task ID, PID, Name, Runtime, and Exit Code.
    Use this to monitor scans, listeners, tunnels, and servers.
    """
    async with _REGISTRY_LOCK:
        if not _TASKS:
            return "No background processes currently tracked."

        lines = [
            f"{'ID':<8} {'PID':<8} {'STATUS':<12} {'RUNTIME':<10} {'NAME':<20} {'COMMAND'}",
            f"{'-'*8} {'-'*8} {'-'*12} {'-'*10} {'-'*20} {'-'*30}",
        ]

        now = time.perf_counter()
        for task_id, task in _TASKS.items():
            if (
                task.status == "RUNNING"
                and task.proc is not None
                and task.proc.returncode is not None
            ):
                task.exit_code = task.proc.returncode
                task.status = "COMPLETED" if task.exit_code == 0 else "FAILED"
                if task.end_time is None:
                    task.end_time = now

            duration_s = (task.end_time or now) - task.start_time
            duration_str = _format_duration(duration_s)

            status_display = task.status
            if task.exit_code is not None and task.status != "RUNNING":
                status_display = f"{task.status}({task.exit_code})"

            pid_str = str(task.pid) if task.pid else "-"
            cmd_preview = task.command if len(task.command) <= 45 else task.command[:42] + "..."

            row = (
                f"{task_id:<8} {pid_str:<8} {status_display:<12} "
                f"{duration_str:<10} {task.name[:19]:<20} {cmd_preview}"
            )
            lines.append(row)

        return "\n".join(lines)


@function_tool(timeout=30)
async def process_output(
    ctx: RunContextWrapper,
    task_id: str,
    lines: int = 50,
    tail: bool = True,
) -> str:
    """Read the output log of a background process.

    Args:
        task_id: The ID of the background task (e.g. "task_1").
        lines: Number of lines to retrieve (default 50).
        tail: True to return the latest/last N lines; False for the first N lines.
    """
    clean_id = task_id.strip()
    task = _TASKS.get(clean_id)
    if not task:
        return f"Error: Task '{task_id}' not found. Use process_list() to see valid task IDs."

    now = time.perf_counter()
    if (
        task.status == "RUNNING"
        and task.proc is not None
        and task.proc.returncode is not None
    ):
        task.exit_code = task.proc.returncode
        task.status = "COMPLETED" if task.exit_code == 0 else "FAILED"
        if task.end_time is None:
            task.end_time = now

    duration_s = (task.end_time or now) - task.start_time
    duration_str = _format_duration(duration_s)

    if not task.log_file.exists():
        return f"[{clean_id} - {task.name} | Status: {task.status}]\n(Log file not found or empty)"

    try:
        with task.log_file.open(encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as exc:
        return f"Error reading log for {clean_id}: {exc}"

    total_lines = len(all_lines)
    count = max(1, min(500, lines))

    if total_lines == 0:
        content = "(No output recorded yet)"
    elif tail:
        content = "".join(all_lines[-count:]).rstrip()
    else:
        content = "".join(all_lines[:count]).rstrip()

    header = (
        f"[{clean_id} ({task.name}) | Status: {task.status} | Runtime: {duration_str} | "
        f"Total Log Lines: {total_lines}]"
    )

    return f"{header}\n\n{content}"


@function_tool(timeout=30)
async def process_kill(ctx: RunContextWrapper, task_id: str) -> str:
    """Terminate a background process gracefully with SIGTERM (and SIGKILL if needed).

    Args:
        task_id: The ID of the background task to terminate.
    """
    clean_id = task_id.strip()
    task = _TASKS.get(clean_id)
    if not task:
        return f"Error: Task '{task_id}' not found. Use process_list() to check tasks."

    if task.status != "RUNNING" or task.proc is None:
        return f"Task '{clean_id}' is not running (Current status: {task.status})."

    try:
        task.proc.terminate()
        try:
            await asyncio.wait_for(task.proc.wait(), timeout=2.0)
        except TimeoutError:
            task.proc.kill()
            await task.proc.wait()

        task.status = "KILLED"
        task.end_time = time.perf_counter()
        task.exit_code = task.proc.returncode
    except ProcessLookupError:
        task.status = "COMPLETED"
        return f"Process {clean_id} had already exited."
    except Exception as exc:  # noqa: BLE001
        return f"Error terminating process {clean_id}: {exc}"
    else:
        return f"Successfully terminated {clean_id} (PID: {task.pid}, Name: {task.name})."


@function_tool(timeout=30)
async def process_write_stdin(
    ctx: RunContextWrapper,
    task_id: str,
    input_text: str,
) -> str:
    """Send text input (stdin) to a running background process.

    Useful for interacting with reverse shells, netcat sessions, or CLI prompts.

    Args:
        task_id: The ID of the background task.
        input_text: Text to write to stdin. Append '\\n' if you want to simulate Enter.
    """
    clean_id = task_id.strip()
    task = _TASKS.get(clean_id)
    if not task:
        return f"Error: Task '{task_id}' not found."

    if task.status != "RUNNING" or task.proc is None:
        return f"Cannot send input: Task '{clean_id}' is not running (Status: {task.status})."

    if task.proc.stdin is None:
        return f"Cannot send input: Process {clean_id} does not have an open stdin pipe."

    try:
        payload = input_text.encode("utf-8")
        task.proc.stdin.write(payload)
        await task.proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        task.status = "COMPLETED"
        return f"Failed to send input: Process {clean_id} has closed its stdin pipe."
    except Exception as exc:  # noqa: BLE001
        return f"Error writing to stdin of {clean_id}: {exc}"
    else:
        return f"Sent {len(payload)} bytes to stdin of {clean_id} (PID: {task.pid})."
