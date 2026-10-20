"""Host-native runtime backend — runs Strix directly on the host, no Docker.

Selected with ``STRIX_RUNTIME_BACKEND=host``. Commands execute on the host as
the Strix process itself (root, when Strix is launched as root), so the agent
gets the full host: real root capabilities for raw-socket tooling, the host
network stack, and the host filesystem.

What this backend is, and what it is not:

* It does **not** sandbox, jail, or confine the agent on Linux. The SDK's
  Unix-local session confines only on macOS (``sandbox-exec``); on Linux the
  command parts are passed through untouched, which is exactly the
  unrestricted host execution this backend exists to provide.
* It does **not** change the agent's system prompt or skills. ``exec_command``
  and the ``Filesystem``/``Shell`` tools are SDK capabilities driven by the same
  session interface the Docker backend satisfies, so the agent's tool surface is
  identical.

Implementation note: this is a thin adapter over the SDK's own
``UnixLocalSandboxClient`` (``agents.sandbox.sandboxes.unix_local``) rather than
a hand-rolled session. That backend already implements the full session contract
— including PTY sessions, so ``write_stdin`` works for REPLs and interactive
tools — and is maintained alongside the SDK. Reimplementing it here would only
fork Strix from upstream fixes.

The one Strix-specific concern is the workspace root. The SDK's Unix-local client
allocates its own ``tempfile.mkdtemp()`` root *unless* the supplied manifest
already carries a non-default root, so this adapter pins ``manifest.root`` to a
concrete host directory before creating the session. That keeps the root stable
at the path Strix hardcodes in ``session_manager`` (extra-file staging, bind
mounts) and in the prompt-facing tooling (``WORKSPACE_SPILL_DIR``), so the agent
is never told about one path while its files live under another.

The root is pinned *in place* on the caller's manifest rather than on a copy:
``session_manager`` keeps that manifest, and a deferred session keeps it as the
pending state it restores over the resolved one, so a copy would leave the
caller's view of the root out of sync with the session that was created.

Unlike Docker, a container root is not guaranteed to exist or be writable for a
non-root user, so the directory is resolved to the first usable candidate rather
than assumed.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agents.sandbox.manifest import Manifest


logger = logging.getLogger(__name__)


# Matches the Docker backend's in-container workspace path. Strix hardcodes
# this string in several places, so it is the root the host backend tries to use.
_WORKSPACE_ROOT = Path("/workspace")

# The SDK's Unix-local client reads this exact string as "the caller did not
# choose a root" and silently swaps in a fresh temp directory instead.
_SDK_DEFAULT_ROOT = "/workspace"


def _resolve_workspace_root() -> Path:
    """Return a writable host directory to use as the sandbox workspace root.

    Preference order:

    1. ``STRIX_HOST_WORKSPACE_ROOT`` — explicit operator override.
    2. ``/workspace`` — the same path Docker uses.
    3. A ``strix-host-workspace`` directory under the system temp dir.

    ``/workspace`` matters more than it looks. Strix builds agent-facing sandbox
    paths as literal ``/workspace/...`` strings (``WORKSPACE_SPILL_DIR`` and the
    prompt's own references), and the SDK's host path policy only accepts paths
    that resolve *under the real root*. So when the root is ``/workspace`` those
    strings are already correct; when it is anything else, Strix's own
    ``/workspace/...`` calls raise ``InvalidManifestPathError``. The fallback
    therefore logs a warning that names the consequence rather than pretending
    the run is equivalent.

    Strix's internal session calls do not depend on this — ``session_manager``
    and the runner read the root back off the session — but the paths the *agent*
    is told about do, which is why a mismatch is a warning and not a silent
    fallback.
    """
    override = os.environ.get("STRIX_HOST_WORKSPACE_ROOT", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if _ensure_writable(candidate):
            if candidate != _WORKSPACE_ROOT:
                _warn_root_mismatch(candidate, "STRIX_HOST_WORKSPACE_ROOT")
            return candidate
        logger.warning("STRIX_HOST_WORKSPACE_ROOT=%s is not usable; falling back", candidate)

    if _ensure_writable(_WORKSPACE_ROOT):
        return _WORKSPACE_ROOT

    fallback = Path(tempfile.gettempdir()) / "strix-host-workspace"
    if _ensure_writable(fallback):
        _warn_root_mismatch(fallback, "/workspace is not writable")
        return fallback

    # Last resort: a per-run directory. Still writable, still stable for the
    # life of the scan, but the mismatch above applies just the same.
    fallback = Path(tempfile.mkdtemp(prefix="strix-host-"))
    _warn_root_mismatch(fallback, "no writable workspace root found")
    return fallback


def _warn_root_mismatch(root: Path, reason: str) -> None:
    """Log that the workspace root is not ``/workspace`` and what that costs.

    Strix's own session calls recover (they read the root off the session), but
    the paths handed to the agent do not: the SDK only accepts paths under the
    real root, so a literal ``/workspace/...`` the agent is told to use will not
    resolve. Running as root makes ``/workspace`` the default and avoids all of
    this.
    """
    logger.warning(
        "Host backend: workspace root is %s (%s), not %s. Strix addresses the "
        "sandbox as %s/... in agent-facing paths, and the SDK only accepts paths "
        "under the real root, so those will not resolve. Run Strix as root for the "
        "closest match to Docker, or point STRIX_HOST_WORKSPACE_ROOT somewhere you "
        "intend the agent to work.",
        root,
        reason,
        _WORKSPACE_ROOT,
        _WORKSPACE_ROOT,
    )


def _ensure_writable(path: Path) -> bool:
    """Create ``path`` if needed and report whether it is usable as a workspace."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def _manifest_root_value(root: Path) -> str:
    """The string to put in ``Manifest.root`` for ``root``.

    The SDK's Unix-local client treats a root equal to its own default
    (``"/workspace"``) as "the caller did not choose a root" and silently
    swaps in a fresh temp directory. That would move the workspace out from
    under the agent-facing ``/workspace/...`` paths Strix hands out, and the
    directory would be deleted on teardown because the SDK owns it.

    A trailing separator names the same directory to every path computation
    (``PurePosixPath("/workspace/")`` is ``/workspace``) but is not the
    sentinel string, so the SDK keeps the root we asked for.
    """
    value = str(root)
    if value == _SDK_DEFAULT_ROOT:
        return f"{value}/"
    return value


async def host_backend(
    *,
    image: str,
    manifest: Manifest,
    exposed_ports: tuple[int, ...],
    bind_mounts: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any]:
    """Bring up a session that runs on the host, without a container.

    Returns the ``(client, session)`` pair ``session_manager.create_or_reuse``
    expects.

    Two arguments are accepted for signature parity with the Docker backend and
    are deliberately unused:

    * ``image`` — nothing is pulled or run. Strix's sandbox image exists to
      provision a toolchain inside a container; on the host the toolchain is
      whatever the host already has.
    * ``bind_mounts`` — under Docker a bind mount makes a host directory appear
      at ``/workspace/<subdir>`` inside the container. Here the host directory
      *is* the workspace, so ``session_manager`` already skips building manifest
      entries for non-bind-mount backends and the "mount" needs no materializing.
    """
    from agents.sandbox.manifest import Environment
    from agents.sandbox.manifest import Manifest as _Manifest
    from agents.sandbox.sandboxes.unix_local import (
        UnixLocalSandboxClient,
        UnixLocalSandboxClientOptions,
    )

    workspace_root = _resolve_workspace_root()
    # Re-root the manifest so the SDK adopts this directory instead of allocating
    # its own temp root. ``Manifest`` is a pydantic model, so ``model_copy``
    # preserves the entries/environment ``session_manager`` built.
    base = manifest if manifest is not None else _Manifest()

    # Browser/MCP tooling writes screenshots and downloads to whatever these
    # point at. The sandbox image sets them under /workspace; without an
    # equivalent here they land in the orchestrator's own CWD, where the agent's
    # ``view_image`` cannot read them (the SDK's path policy only accepts paths
    # under the workspace root) — so evidence silently becomes unreachable.
    screenshot_dir = workspace_root / ".agent-browser-screenshots"
    downloads_dir = workspace_root / ".agent-browser-downloads"
    for directory in (screenshot_dir, downloads_dir):
        directory.mkdir(parents=True, exist_ok=True)

    merged_env: dict[str, Any] = {
        **base.environment.value,
        "AGENT_BROWSER_SCREENSHOT_DIR": str(screenshot_dir),
        "AGENT_BROWSER_DOWNLOAD_DIR": str(downloads_dir),
    }
    # Mutated in place rather than copied. The caller (``session_manager``)
    # keeps a reference to this manifest — and a deferred session keeps it as
    # the *pending* state it restores over the resolved one — so a copy would
    # leave the caller's view of the root and the environment out of sync with
    # the session that was actually created.
    base.root = _manifest_root_value(workspace_root)
    base.environment = Environment(value=merged_env)
    manifest = base

    client = UnixLocalSandboxClient()
    options = UnixLocalSandboxClientOptions(exposed_ports=exposed_ports)
    session = await client.create(options=options, manifest=manifest)

    # ``start()`` is what materializes the manifest into the workspace. The SDK's
    # own Unix-local client does not call it, but Strix's Docker backend must
    # (see ``_docker_backend``) for exactly this reason — without it, the
    # ``local_sources`` trees that ``session_manager`` puts in ``Manifest.entries``
    # never appear in the workspace and a code-review scan finds an empty repo.
    # Calling it here is what makes host mode see the target source.
    await session.start()

    actual_root = str(session._inner.state.manifest.root)
    if actual_root.rstrip("/") != str(workspace_root).rstrip("/"):
        # The SDK moved the workspace anyway (it swaps in its own temp dir when
        # it thinks no root was chosen). Say so: the agent-facing paths Strix
        # advertises no longer describe where the files actually are.
        _warn_root_mismatch(Path(actual_root), "the sandbox client relocated the workspace")

    logger.info(
        "Host session ready: workspace_root=%s exposed_ports=%s",
        actual_root,
        list(exposed_ports),
    )
    return client, session
