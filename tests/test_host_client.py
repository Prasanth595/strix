"""Tests for the host (no-Docker) runtime backend.

The host backend is a thin adapter over the SDK's Unix-local sandbox client.
What is worth pinning down here is the Strix-specific behaviour: choosing a
writable workspace root, re-rooting the manifest so the SDK adopts it, and
calling ``start()`` so the manifest's entries actually materialize.

The SDK's own session behaviour (exec, filesystem ops, PTY) is upstream's
contract and is not duplicated here.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from agents.sandbox.entries import LocalDir
from agents.sandbox.manifest import Environment, Manifest

from strix.config.settings import RuntimeSettings
from strix.runtime.backends import (
    backend_supports_bind_mounts,
    get_backend,
    register_backend,
    supported_backends,
)
from strix.runtime.host_client import (
    _manifest_root_value,
    _resolve_workspace_root,
    _warn_root_mismatch,
    host_backend,
)


def test_host_backend_is_registered() -> None:
    assert "host" in supported_backends()
    assert get_backend("host") is not None


def test_host_is_the_default_backend() -> None:
    """Host-native is the default; ``docker`` stays reachable by name.

    This is the security-relevant default: with no ``STRIX_RUNTIME_BACKEND`` set,
    agent commands run on the operator's machine with no isolation. Docker must
    remain a selectable value so isolation can be restored.
    """
    assert RuntimeSettings().backend == "host"
    assert RuntimeSettings(STRIX_RUNTIME_BACKEND="docker").backend == "docker"
    assert get_backend("docker") is not None


@pytest.mark.asyncio
async def test_default_backend_warns_about_no_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A default that removes isolation must not fail silently."""
    monkeypatch.setenv("STRIX_HOST_WORKSPACE_ROOT", str(tmp_path / "ws"))
    manifest = Manifest(entries={}, environment=Environment(value={}))

    with caplog.at_level("WARNING"):
        client, session = await get_backend("host")(
            image="unused",
            manifest=manifest,
            exposed_ports=(),
        )
    await client.delete(session)

    assert "no isolation" in caplog.text


def test_host_backend_does_not_claim_bind_mounts() -> None:
    # A bind mount is a container concept; on the host the directory already is
    # the workspace. Claiming support would make session_manager build manifest
    # entries it then has to copy unnecessarily.
    assert backend_supports_bind_mounts("host") is False


def test_unknown_backend_still_raises() -> None:
    with pytest.raises(ValueError, match="Unknown STRIX_RUNTIME_BACKEND"):
        get_backend("nope")


def test_resolve_workspace_root_prefers_override(tmp_path: Path) -> None:
    target = tmp_path / "custom-root"
    os.environ["STRIX_HOST_WORKSPACE_ROOT"] = str(target)
    try:
        assert _resolve_workspace_root() == target
        assert target.is_dir()
    finally:
        del os.environ["STRIX_HOST_WORKSPACE_ROOT"]


def test_resolve_workspace_root_falls_back_when_override_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A file where a directory is needed: mkdir fails, so the override is
    # unusable and resolution must continue rather than return it.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("STRIX_HOST_WORKSPACE_ROOT", str(blocker))
    monkeypatch.setattr("strix.runtime.host_client._WORKSPACE_ROOT", tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    assert _resolve_workspace_root() == tmp_path / "ws"


def test_warn_root_mismatch_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    elsewhere = tmp_path / "elsewhere"
    with caplog.at_level("WARNING"):
        _warn_root_mismatch(elsewhere, "test")
    assert "not /workspace" in caplog.text


def test_manifest_root_value_pins_workspace() -> None:
    """``/workspace`` must be asked for in a form the SDK will not override.

    The SDK's Unix-local client reads a root equal to its own default as "no
    root chosen" and swaps in a temp directory. The trailing separator names
    the same directory to every path computation but is not that sentinel.
    """
    assert _manifest_root_value(Path("/workspace")) == "/workspace/"
    assert _manifest_root_value(Path("/srv/strix-workspace")) == "/srv/strix-workspace"


@pytest.mark.asyncio
async def test_host_backend_updates_the_callers_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest the caller holds must describe the session that was made.

    ``session_manager`` keeps that manifest, and a deferred session restores it
    over the resolved one. If the backend worked on a copy, the caller's root
    and environment would disagree with the live session.
    """
    root = tmp_path / "host-ws"
    monkeypatch.setenv("STRIX_HOST_WORKSPACE_ROOT", str(root))

    manifest = Manifest(entries={}, environment=Environment(value={"KEEP": "1"}))
    client, session = await host_backend(
        image="unused:image",
        manifest=manifest,
        exposed_ports=(),
    )
    try:
        assert manifest.root.rstrip("/") == str(root)
        assert manifest.environment.value["KEEP"] == "1"
        assert manifest.environment.value["AGENT_BROWSER_SCREENSHOT_DIR"] == str(
            root / ".agent-browser-screenshots"
        )
    finally:
        await client.delete(session)


@pytest.mark.asyncio
async def test_host_backend_reroots_manifest_and_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest root must become the real host dir, and start() must run.

    Both matter: a manifest left at the SDK default makes the SDK swap in its own
    temp directory (so Strix's ``/workspace`` paths stop resolving), and skipping
    ``start()`` leaves the manifest's entries unmaterialized — a code-review scan
    would find an empty workspace.

    The source tree lives under the CWD on purpose. The SDK resolves
    ``LocalDir.src`` against the process working directory and rejects anything
    outside it unless a path grant widens the base, so a ``tmp_path`` fixture
    cannot stand in for a real scan target here.
    """
    root = tmp_path / "host-ws"
    monkeypatch.setenv("STRIX_HOST_WORKSPACE_ROOT", str(root))

    source = Path.cwd() / ".test-fixture-src"
    shutil.rmtree(source, ignore_errors=True)
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "mod.py").write_text("x = 1\n")

    manifest = Manifest(
        entries={"repo": LocalDir(src=source)},
        environment=Environment(value={"STRIX_PROBE": "1"}),
    )
    try:
        client, session = await host_backend(
            image="unused:image",
            manifest=manifest,
            exposed_ports=(48080,),
        )
        try:
            inner: Any = session._inner
            assert inner.state.manifest.root == str(root)

            # start() ran, so the manifest entry was materialized under the root.
            assert (root / "repo" / "pkg" / "mod.py").read_text() == "x = 1\n"

            result = await session.exec("sh", "-c", "echo $STRIX_PROBE")
            assert result.stdout.decode().strip() == "1"

            # The workspace root is the exec cwd, and the port resolves.
            assert (await session.exec("pwd")).stdout.decode().strip() == str(root)
            endpoint = await session.resolve_exposed_port(48080)
            assert (endpoint.host, endpoint.port) == ("127.0.0.1", 48080)
        finally:
            await client.delete(session)
    finally:
        shutil.rmtree(source, ignore_errors=True)


@pytest.mark.asyncio
async def test_host_backend_delete_leaves_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a scan must not mean deleting the user's files."""
    root = tmp_path / "kept-ws"
    monkeypatch.setenv("STRIX_HOST_WORKSPACE_ROOT", str(root))

    client, session = await host_backend(
        image="unused:image",
        manifest=Manifest(entries={}, environment=Environment(value={})),
        exposed_ports=(),
    )
    marker = root / "keep-me.txt"
    marker.write_text("still here")

    await client.delete(session)
    assert marker.read_text() == "still here"


def test_register_backend_is_reversible() -> None:
    """A downstream host backend must be able to shadow ours, as the API says."""

    async def custom(**_kwargs: Any) -> tuple[Any, Any]:  # pragma: no cover - not called
        return None, None

    register_backend("host", custom)
    try:
        assert get_backend("host") is custom
    finally:
        register_backend("host", get_backend("host"))
