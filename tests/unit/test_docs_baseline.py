# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""The operator docs describe Alloy ownership and adoption."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _section(text, heading):
    start = text.index(f"\n{heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _readme_section():
    return _section((ROOT / "README.md").read_text(encoding="utf-8"), "## One Alloy per machine")


def test_readme_describes_one_alloy_per_machine_before_alert_rule_transport():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.index("\n## One Alloy per machine\n") < readme.index(
        "\n## Alert rule transport\n"
    )


def test_readme_section_names_the_statuses_the_action_and_the_marker():
    section = _readme_section()

    for expected in (
        "Alloy on this machine is managed by <unit>; remove this unit or <unit>",
        "unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy",
        "juju run alloy-vm/0 adopt-alloy",
        "force=true",
        "/var/lib/juju-alloy/owner.json",
        "/etc/alloy/config.alloy.package-default",
        "alloy-sub",
        "machine-observability",
        "apt-get purge alloy",
    ):
        assert expected in section, expected


def test_architecture_doc_mentions_the_marker_and_the_action():
    architecture = (ROOT / "docs" / "charm-architecture.md").read_text(encoding="utf-8")

    assert "/var/lib/juju-alloy/owner.json" in architecture
    assert "adopt-alloy" in architecture
