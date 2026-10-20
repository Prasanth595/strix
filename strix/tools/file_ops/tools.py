"""File operations tools — direct, reliable file read and write."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from agents import RunContextWrapper, function_tool

from strix.runtime.session_manager import session_workspace_root


logger = logging.getLogger(__name__)

_DEFAULT_MAX_LINES = 1000
_BINARY_CHECK_BYTES = 4096


def _resolve_target_path(path_str: str, ctx: RunContextWrapper | None = None) -> Path:
    """Resolve a target path against workspace root or host CWD."""
    p_str = path_str.strip()
    if p_str.startswith("~"):
        return Path(p_str).expanduser().resolve()

    p = Path(p_str)
    if p.is_absolute():
        return p.resolve()

    workspace: Path | None = None
    if ctx is not None and isinstance(ctx.context, dict):
        session = ctx.context.get("sandbox_session")
        if session is not None:
            with contextlib.suppress(Exception):
                root_str = session_workspace_root(session)
                if root_str:
                    workspace = Path(root_str).resolve()

    cwd = Path.cwd().resolve()
    if workspace is not None and workspace.exists():
        candidate = (workspace / p).resolve()
        if candidate.exists() or not (cwd / p).exists():
            return candidate

    return (cwd / p).resolve()


def _is_binary(chunk: bytes) -> bool:
    """Check if byte chunk represents binary content."""
    return b"\x00" in chunk


def _read_directory(resolved: Path) -> str:
    """Return formatted directory entries."""
    try:
        entries = sorted(resolved.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        lines = [f"[Directory: {resolved} ({len(entries)} entries)]"]
        for entry in entries:
            prefix = "📁 " if entry.is_dir() else "📄 "
            size_str = ""
            if not entry.is_dir():
                with contextlib.suppress(OSError):
                    size_str = f" ({entry.stat().st_size:,} bytes)"
            lines.append(f"  {prefix}{entry.name}{size_str}")
        return "\n".join(lines)
    except PermissionError:
        return f"Error: Permission denied accessing directory: {resolved}"
    except OSError as exc:
        return f"Error reading directory {resolved}: {exc}"


def _read_file_content(
    resolved: Path,
    start_line: int | None,
    end_line: int | None,
    max_lines: int | None,
    path_str: str,
) -> str:
    """Read and format text lines from a file."""
    try:
        with resolved.open("rb") as f:
            chunk = f.read(_BINARY_CHECK_BYTES)
        if _is_binary(chunk):
            size = resolved.stat().st_size
            hex_preview = chunk[:64].hex(" ", 1)
            return (
                f"[Binary File: {resolved} | Size: {size:,} bytes]\n"
                f"Binary content detected. First 64 bytes (hex):\n{hex_preview}"
            )
        with resolved.open(encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()
    except PermissionError:
        return f"Error: Permission denied reading file: {resolved}"
    except OSError as exc:
        return f"Error reading file {resolved}: {exc}"

    total_lines = len(raw_lines)
    if total_lines == 0:
        return f"[File: {resolved} is empty (0 bytes)]"

    start = max(1, min(start_line, total_lines)) if start_line is not None else 1
    end = max(start, min(end_line, total_lines)) if end_line is not None else total_lines
    limit = _DEFAULT_MAX_LINES if max_lines is None or max_lines <= 0 else max_lines

    if (end - start + 1) > limit:
        truncated_end = start + limit - 1
        is_truncated = True
    else:
        truncated_end = end
        is_truncated = False

    selected_lines = raw_lines[start - 1 : truncated_end]
    size = resolved.stat().st_size
    header = (
        f"[File: {resolved} | Lines {start}-{truncated_end} of {total_lines} | "
        f"Size: {size:,} bytes]"
    )
    output_parts = [header]

    digits = len(str(truncated_end))
    for idx, line in enumerate(selected_lines, start=start):
        output_parts.append(f"{idx:>{digits}} | {line.rstrip('\r\n')}")

    if is_truncated:
        notice = (
            f"\n[Note: Output capped at {limit} lines. Call "
            f"read_file(path='{path_str}', start_line={truncated_end + 1}) to read further.]"
        )
        output_parts.append(notice)

    return "\n".join(output_parts)


@function_tool(timeout=30)
async def read_file(
    ctx: RunContextWrapper,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int | None = None,
) -> str:
    """Read contents of a file with line numbering and range slicing.

    Supports reading any file (source code, configuration, scripts, logs, loot, flags).
    If the path is a directory, returns a formatted directory listing instead.
    Avoids terminal buffer truncation or shell pipe escaping issues.

    Args:
        path: Path to the file (absolute, or relative to current working directory/workspace).
        start_line: 1-indexed starting line number (optional).
        end_line: 1-indexed ending line number (optional).
        max_lines: Maximum number of lines to return (default 1000).
    """
    if not path or not path.strip():
        return "Error: Path cannot be empty."

    resolved = _resolve_target_path(path, ctx)
    if not resolved.exists():
        return f"Error: File not found: {resolved}"

    if resolved.is_dir():
        return _read_directory(resolved)

    return _read_file_content(resolved, start_line, end_line, max_lines, path)


@function_tool(timeout=30)
async def write_file(
    ctx: RunContextWrapper,
    path: str,
    content: str,
    mode: str = "overwrite",
) -> str:
    """Write or append text content to a file.

    Guarantees exact byte-for-byte content without bash heredoc escaping bugs
    (such as '$', quotes, or backticks being mangled). Parent directories
    are created automatically if they do not exist.

    Args:
        path: Destination file path (absolute, or relative to current working directory/workspace).
        content: The exact text content to write.
        mode: Write mode: "overwrite" (default) or "append".
    """
    if not path or not path.strip():
        return "Error: Path cannot be empty."

    norm_mode = (mode or "overwrite").strip().lower()
    if norm_mode not in ("overwrite", "append"):
        return f"Error: Invalid mode '{mode}'. Use 'overwrite' or 'append'."

    resolved = _resolve_target_path(path, ctx)

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        file_mode = "w" if norm_mode == "overwrite" else "a"
        with resolved.open(file_mode, encoding="utf-8") as f:
            f.write(content)
        byte_count = len(content.encode("utf-8"))
        line_count = len(content.splitlines())
        total_size = resolved.stat().st_size
    except PermissionError:
        return f"Error: Permission denied writing to: {resolved}"
    except OSError as exc:
        return f"Error writing to {resolved}: {exc}"
    else:
        return (
            f"Successfully wrote {byte_count:,} bytes ({line_count:,} lines) to {resolved} "
            f"(mode: {norm_mode}, total file size: {total_size:,} bytes)."
        )
