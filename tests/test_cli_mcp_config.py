"""Tests for the --mcp-config CLI flag."""

from __future__ import annotations

import importlib
import os
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from strix.tools.mcp.loader import load_user_mcp_configs


if TYPE_CHECKING:
    from pathlib import Path


cli_main: Any = importlib.import_module("strix.interface.main")


def _stub_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_main,
        "load_settings",
        lambda: SimpleNamespace(runtime=SimpleNamespace(max_local_copy_mb=1024)),
    )


def test_mcp_config_flag_sets_loader_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "servers.json"
    config.write_text("[]", encoding="utf-8")
    _stub_settings(monkeypatch)
    # delenv records "originally absent" so monkeypatch removes whatever the
    # parser sets, keeping the override from leaking into other tests.
    monkeypatch.delenv("STRIX_MCP_CONFIG", raising=False)
    monkeypatch.setattr(
        sys, "argv", ["strix", "-t", "https://test.com/", "-n", "--mcp-config", str(config)]
    )

    args = cli_main.parse_arguments()

    assert args.mcp_config == str(config)
    assert os.environ["STRIX_MCP_CONFIG"] == str(config)


def test_mcp_config_flag_rejects_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_settings(monkeypatch)
    monkeypatch.delenv("STRIX_MCP_CONFIG", raising=False)
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(
        sys, "argv", ["strix", "-t", "https://test.com/", "-n", "--mcp-config", str(missing)]
    )

    with pytest.raises(SystemExit):
        cli_main.parse_arguments()

    assert "--mcp-config file not found" in capsys.readouterr().err


def test_mcp_server_flags_set_selection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_settings(monkeypatch)
    monkeypatch.delenv("STRIX_MCP_ONLY", raising=False)
    monkeypatch.delenv("STRIX_MCP_EXCLUDE", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "strix",
            "-t",
            "https://test.com/",
            "-n",
            "--mcp-server",
            "a",
            "--mcp-server",
            "b",
            "--mcp-exclude",
            "c",
        ],
    )

    cli_main.parse_arguments()

    assert os.environ["STRIX_MCP_ONLY"] == "a,b"
    assert os.environ["STRIX_MCP_EXCLUDE"] == "c"


def test_load_user_mcp_configs_dict_format(tmp_path: Path) -> None:
    config_file = tmp_path / "dict_mcp.json"
    config_file.write_text(
        """
        {
          "rea": {
            "type": "local",
            "command": ["npx", "-y", "rea-agents@latest", "mcp"],
            "enabled": true,
            "environment": {
              "GHIDRA_INSTALL_DIR": "/tools/ghidra",
              "JAVA_HOME": "/usr/lib/jvm/java-21"
            }
          },
          "disabled_srv": {
            "command": "echo",
            "enabled": false
          }
        }
        """,
        encoding="utf-8",
    )

    configs = load_user_mcp_configs(config_file)
    assert len(configs) == 1
    assert configs[0].name == "rea"
    assert configs[0].command == "npx"
    assert configs[0].args == ["-y", "rea-agents@latest", "mcp"]
    assert configs[0].env["GHIDRA_INSTALL_DIR"] == "/tools/ghidra"
    assert configs[0].env["JAVA_HOME"] == "/usr/lib/jvm/java-21"
