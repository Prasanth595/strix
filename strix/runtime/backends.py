"""Sandbox backend registry — selected via STRIX_RUNTIME_BACKEND (default: host)."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from agents.sandbox.manifest import Manifest


logger = logging.getLogger(__name__)


SandboxBackend = Callable[..., Awaitable[tuple[Any, Any]]]


async def _docker_backend(
    *,
    image: str,
    manifest: Manifest,
    exposed_ports: tuple[int, ...],
    bind_mounts: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any]:
    """Bring up a session backed by the local Docker daemon.

    Uses :class:`StrixDockerSandboxClient` to inject NET_ADMIN /
    NET_RAW caps + ``host.docker.internal`` host-gateway, on the same
    endpoint the startup check resolved (``DOCKER_HOST``, docker context,
    default socket). Imports ``docker`` lazily so deployments that target
    a non-Docker backend don't need the docker-py library installed.

    ``session.start()`` is what materializes the manifest into the running
    container — the SDK's ``client.create()`` only builds the inner session
    object without applying it. ``async with session:`` would call it too, but
    Strix manages session lifetime explicitly via ``client.delete()`` so we
    trigger ``start()`` ourselves.
    """
    from agents.sandbox.sandboxes.docker import DockerSandboxClientOptions

    from strix.runtime.docker_client import StrixDockerSandboxClient
    from strix.runtime.docker_connection import connect_docker

    client = StrixDockerSandboxClient(connect_docker())
    client.strix_bind_mounts = bind_mounts or []
    options = DockerSandboxClientOptions(image=image, exposed_ports=exposed_ports)
    session = await client.create(options=options, manifest=manifest)
    await session.start()
    return client, session


async def _host_backend(
    *,
    image: str,
    manifest: Manifest,
    exposed_ports: tuple[int, ...],
    bind_mounts: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any]:
    """Bring up a session backed by the host itself — no container involved.

    Commands run as the Strix process (root, when Strix is launched as root),
    so the agent has full host privileges: real capabilities for raw-socket
    tools, the host network stack, and the host filesystem.

    ``image`` is accepted to keep the shared backend signature but is unused;
    nothing is pulled or run. ``bind_mounts`` is ignored for the same reason
    ``create_or_reuse`` skips manifest entries for non-bind-mount backends: the
    host directory is already the workspace.

    Imports are lazy so a Docker-only deployment never loads this module.
    """
    from strix.runtime.host_client import host_backend

    # This is the default backend, so this line runs on every scan. It says the
    # one thing an operator needs to know before an agent that has read a
    # malicious target starts running commands as them.
    logger.warning(
        "Host runtime backend: agent commands run directly on this host as the Strix "
        "process (uid %s) with no isolation — a prompt-injected target could act with "
        "those privileges. Set STRIX_RUNTIME_BACKEND=docker for container isolation.",
        os.getuid() if hasattr(os, "getuid") else "n/a",
    )
    return await host_backend(
        image=image,
        manifest=manifest,
        exposed_ports=exposed_ports,
        bind_mounts=bind_mounts,
    )


_BACKENDS: dict[str, SandboxBackend] = {
    "docker": _docker_backend,
    "host": _host_backend,
}

_BIND_MOUNT_BACKENDS: set[str] = {"docker"}


def get_backend(name: str) -> SandboxBackend:
    """Return the backend factory for ``name`` or raise.

    Args:
        name: Backend identifier (e.g. ``"docker"``). Match is exact;
            no fallback. Unknown values raise so config typos surface
            immediately instead of silently picking a default.
    """
    backend = _BACKENDS.get(name)
    if backend is None:
        supported = ", ".join(sorted(_BACKENDS))
        raise ValueError(
            f"Unknown STRIX_RUNTIME_BACKEND: {name!r} (supported: {supported})",
        )
    logger.debug("Selected sandbox backend: %s", name)
    return backend


def register_backend(
    name: str,
    backend: SandboxBackend,
    *,
    supports_bind_mounts: bool = False,
) -> None:
    """Register a custom backend under ``name``.

    Intended for downstream users who ship their own runtime — register
    before any ``session_manager.create_or_reuse`` call. Re-registering
    an existing name overwrites the prior entry. ``supports_bind_mounts``
    defaults to False: a remote runtime cannot see the caller's filesystem, so
    it is handed local sources as manifest entries to upload instead.
    """
    _BACKENDS[name] = backend
    if supports_bind_mounts:
        _BIND_MOUNT_BACKENDS.add(name)
    else:
        _BIND_MOUNT_BACKENDS.discard(name)
    logger.info("Registered sandbox backend: %s (bind mounts: %s)", name, supports_bind_mounts)


def backend_supports_bind_mounts(name: str) -> bool:
    return name in _BIND_MOUNT_BACKENDS


def supported_backends() -> list[str]:
    return sorted(_BACKENDS)
