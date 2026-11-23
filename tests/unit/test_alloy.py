# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

import shutil
import subprocess

import pytest

import alloy


def _completed(*, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["alloy", "--version"],
        returncode=0,
        stdout=stdout,
        stderr=stderr,
    )


def test_get_version_parses_v_prefix(monkeypatch):
    monkeypatch.setattr(
        alloy,
        "_run",
        lambda *_args, **_kwargs: _completed(
            stdout="alloy, version v1.12.2 (branch: HEAD, revision: 477e314)\n"
        ),
    )

    assert alloy.get_version() == "1.12.2"


def test_get_version_parses_plain_version(monkeypatch):
    monkeypatch.setattr(
        alloy,
        "_run",
        lambda *_args, **_kwargs: _completed(stdout="alloy, version 1.12.2\n"),
    )

    assert alloy.get_version() == "1.12.2"


def test_get_version_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(
        alloy,
        "_run",
        lambda *_args, **_kwargs: _completed(stdout="alloy build info\n"),
    )

    assert alloy.get_version() is None


def test_read_custom_args(tmp_path):
    defaults = tmp_path / "alloy-defaults"
    defaults.write_text(
        'CONFIG_FILE="/etc/alloy/config.alloy"\n'
        'CUSTOM_ARGS="--server.http.listen-addr=0.0.0.0:6987"\n',
        encoding="utf-8",
    )

    assert alloy.read_custom_args(defaults_path=defaults) == (
        "--server.http.listen-addr=0.0.0.0:6987"
    )


def test_write_custom_args_updates_existing(tmp_path):
    defaults = tmp_path / "alloy-defaults"
    defaults.write_text(
        'CONFIG_FILE="/etc/alloy/config.alloy"\n'
        'CUSTOM_ARGS="--server.http.listen-addr=0.0.0.0:6987"\n',
        encoding="utf-8",
    )

    alloy.write_custom_args(
        "--server.http.listen-addr=0.0.0.0:12345",
        defaults_path=defaults,
    )

    content = defaults.read_text(encoding="utf-8")
    assert 'CUSTOM_ARGS="--server.http.listen-addr=0.0.0.0:12345"' in content
    assert 'CONFIG_FILE="/etc/alloy/config.alloy"' in content


def test_write_custom_args_removes_line_when_none(tmp_path):
    defaults = tmp_path / "alloy-defaults"
    defaults.write_text(
        'CONFIG_FILE="/etc/alloy/config.alloy"\n'
        'CUSTOM_ARGS="--server.http.listen-addr=0.0.0.0:6987"\n',
        encoding="utf-8",
    )

    alloy.write_custom_args(None, defaults_path=defaults)

    content = defaults.read_text(encoding="utf-8")
    assert "CUSTOM_ARGS=" not in content
    assert 'CONFIG_FILE="/etc/alloy/config.alloy"' in content


def test_install_keeps_modified_conffiles_for_every_package_transaction(monkeypatch):
    commands = []

    def package_manager(cmd, **kwargs):
        commands.append(cmd)
        if cmd[0] == "apt-get" and "install" in cmd:
            # Installing dependencies also configures pending packages such as Alloy.
            assert "Dpkg::Options::=--force-confold" in cmd
        return _completed()

    monkeypatch.setattr(alloy, "_run", package_manager)
    monkeypatch.setattr(alloy, "_run_shell", lambda *_args, **_kwargs: _completed())
    alloy.install()
    installs = [cmd for cmd in commands if cmd[0] == "apt-get" and "install" in cmd]
    assert [cmd[-2:] for cmd in installs] == [["wget", "gpg"], ["-y", "alloy"]]


@pytest.mark.skipif(not shutil.which("dpkg-deb"), reason="requires Debian package tools")
def test_dpkg_recovers_interrupted_conffile_upgrade_without_replacing_settings(tmp_path):
    root = tmp_path / "root"
    admin = root / "var/lib/dpkg"
    admin.mkdir(parents=True)
    (admin / "status").touch()

    def dpkg(*args, check=True):
        return subprocess.run(
            ["dpkg", f"--root={root}", "--force-not-root", *args],
            input="",
            text=True,
            capture_output=True,
            check=check,
        )

    for version in ["1", "2"]:
        package = tmp_path / f"package-{version}"
        (package / "DEBIAN").mkdir(parents=True)
        (package / "etc/default").mkdir(parents=True)
        (package / "DEBIAN/control").write_text(
            f"Package: conffile-recovery-test\nVersion: {version}\n"
            "Architecture: all\nMaintainer: Test <test@example.com>\n"
            "Description: Isolated conffile recovery regression\n"
        )
        (package / "DEBIAN/conffiles").write_text("/etc/default/alloy\n")
        (package / "etc/default/alloy").write_text(f"package defaults {version}\n")
        archive = tmp_path / f"package-{version}.deb"
        subprocess.run(
            ["dpkg-deb", "--build", "--root-owner-group", str(package), str(archive)],
            check=True,
            capture_output=True,
        )
        if version == "1":
            dpkg("--install", str(archive))
            (root / "etc/default/alloy").write_text("charm managed defaults\n")
        else:
            dpkg("--unpack", str(archive))

    failed = dpkg("--configure", "conffile-recovery-test", check=False)
    assert failed.returncode != 0
    assert "end of file on stdin at conffile prompt" in failed.stderr
    dpkg("--force-confold", "--configure", "conffile-recovery-test")
    assert (root / "etc/default/alloy").read_text() == "charm managed defaults\n"
    assert dpkg("--audit").stdout == ""
