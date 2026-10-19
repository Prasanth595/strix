"""Tests for symlink-escape vulnerability in check_mountable_dir.

GHSA-m7vc-4788-7h84: check_mountable_dir must reject directories that contain
symlinks pointing into forbidden trees (e.g. /etc, /usr, /root, etc.).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strix.interface.utils import check_mountable_dir


def test_check_mountable_dir_rejects_symlink_to_etc(tmp_path: Path) -> None:
    """A project directory with an etc/ symlink pointing to /etc must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    etc_link = project / "etc"
    etc_link.symlink_to("/etc")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_usr(tmp_path: Path) -> None:
    """A project directory with a usr/ symlink pointing to /usr must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    usr_link = project / "usr"
    usr_link.symlink_to("/usr")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_home_via_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project directory with a symlink pointing to home must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    home_link = project / "home"
    home_link.symlink_to(home)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_deep_symlink_to_etc(tmp_path: Path) -> None:
    """A symlink deep inside the project tree pointing to /etc must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    etc_link = src / "etc"
    etc_link.symlink_to("/etc")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_aws_creds(tmp_path: Path) -> None:
    """A symlink to .aws (credential dir) must be rejected."""
    real_aws = tmp_path / ".aws"
    real_aws.mkdir()
    project = tmp_path / "myproject"
    project.mkdir()
    aws_link = project / ".aws_link"
    aws_link.symlink_to(real_aws)

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_accepts_normal_symlinks(tmp_path: Path) -> None:
    """Symlinks to safe locations (within the project) are fine."""
    project = tmp_path / "myproject"
    project.mkdir()
    # symlink within the project itself is safe
    real_dir = project / "real"
    real_dir.mkdir()
    link = project / "link_to_real"
    link.symlink_to(real_dir)

    check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_root(tmp_path: Path) -> None:
    """A symlink pointing to filesystem root must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    root_link = project / "root"
    root_link.symlink_to("/")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_var(tmp_path: Path) -> None:
    """A symlink pointing to /var must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    var_link = project / "var"
    var_link.symlink_to("/var")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_lib(tmp_path: Path) -> None:
    """A symlink pointing to /lib must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    lib_link = project / "lib"
    lib_link.symlink_to("/lib")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_nix_store(tmp_path: Path) -> None:
    """A symlink pointing to /nix/store must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    nix_link = project / "nix"
    nix_link.symlink_to("/nix/store")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_library(tmp_path: Path) -> None:
    """A symlink pointing to /Library must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    lib_link = project / "Library"
    lib_link.symlink_to("/Library")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)


def test_check_mountable_dir_rejects_symlink_to_applications(tmp_path: Path) -> None:
    """A symlink pointing to /Applications must be rejected."""
    project = tmp_path / "myproject"
    project.mkdir()
    app_link = project / "Applications"
    app_link.symlink_to("/Applications")

    with pytest.raises(ValueError, match="Refusing to mount"):
        check_mountable_dir(project)
