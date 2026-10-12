import argparse
from pathlib import Path
import pytest

from strix.interface.cli_args import parse_arguments, load_resume_state


def test_subagent_model_default_none(monkeypatch):
    """Verify --subagent-model defaults to None when omitted."""
    monkeypatch.setattr("sys.argv", ["strix", "--target", "https://example.com"])
    args = parse_arguments()
    assert args.subagent_model is None


def test_subagent_model_flag_parsed(monkeypatch):
    """Verify --subagent-model accepts a model name."""
    monkeypatch.setattr(
        "sys.argv",
        ["strix", "--target", "https://example.com", "--subagent-model", "claude-3-5-haiku"],
    )
    args = parse_arguments()
    assert args.subagent_model == "claude-3-5-haiku"


def test_resume_restores_subagent_model(monkeypatch, tmp_path):
    """Verify _load_resume_state restores persisted subagent_model."""
    fake_state = {
        "targets_info": [{"type": "domain", "original": "example.com", "details": {}}],
        "subagent_model": "gpt-4o-mini",
        "scan_mode": "deep",
    }

    args = argparse.Namespace(
        resume="test-run",
        subagent_model=None,
        scan_mode="deep",
        diff_scope="auto",
        targets_info=[],
        workspace_mount=None,
        instruction=None,
        local_sources=[],
    )
    parser = argparse.ArgumentParser()

    monkeypatch.setattr("strix.report.writer.read_run_record", lambda run_dir: fake_state)
    monkeypatch.setattr("strix.interface.cli_args.run_dir_for", lambda r: tmp_path)
    monkeypatch.setattr(Path, "exists", lambda self: True)

    load_resume_state(args)
    assert args.subagent_model == "gpt-4o-mini"
