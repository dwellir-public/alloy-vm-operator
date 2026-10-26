import json
import re
from typing import Any, cast

import pytest

from alert_rules import validate_rule_groups
from host_alert_rules import host_filesystem_rule_groups

TOPOLOGY = {
    "juju_model": "base-mainnet-full",
    "juju_model_uuid": "00000000-0000-4000-8000-000000000222",
    "juju_application": "alloy-vm",
}


def _groups():
    return host_filesystem_rule_groups(
        model=TOPOLOGY["juju_model"],
        model_uuid=TOPOLOGY["juju_model_uuid"],
        application=TOPOLOGY["juju_application"],
        charm_name="alloy-vm",
    )


def _rules() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _groups()[0]["rules"])


def test_host_filesystem_rules_are_valid_and_scoped_to_own_application():
    groups = _groups()

    assert validate_rule_groups(groups)
    assert [group["name"] for group in groups] == [
        f"{TOPOLOGY['juju_model_uuid']}-alloy-vm-host-filesystem"
    ]
    for rule in _rules():
        for key, value in TOPOLOGY.items():
            assert f"{key}={json.dumps(value)}" in rule["expr"]
            assert rule["labels"][key] == value
        assert rule["labels"]["juju_charm"] == "alloy-vm"
        assert rule["labels"]["severity"] in {"warning", "critical"}
        gra_id = re.match(r"\[(GRA-\d{4})\] ", rule["annotations"]["summary"])
        assert gra_id is not None
        slug = gra_id.group(1).lower().replace("-", "_")
        assert rule["annotations"]["runbook_url"].startswith(
            f"https://docs.internal.dwellir.com/docs/operations/grafana_alert_runbooks/{slug}_"
        )


def _promql_regex(expr: str, label: str) -> str:
    """Extract one negative-matcher regex from a rendered expression, unescaping PromQL."""
    match = re.search(rf'{label}!~"((?:[^"\\]|\\.)*)"', expr)
    assert match is not None
    return match.group(1).replace("\\\\", "\\")


@pytest.mark.parametrize(
    ("mountpoint", "excluded"),
    [
        ("/", False),
        ("/mnt/blockchaindata0", False),
        ("/boot/efi", False),
        ("/runtime", False),
        ("/system-data", False),
        ("/run", True),
        ("/run/lock", True),
        ("/sys/fs/cgroup", True),
        ("/dev/shm", True),
        ("/snap/core22/1234", True),
        ("/var/snap/lxd/common/lxd", False),
    ],
)
def test_mountpoint_exclusions_respect_directory_boundaries(mountpoint, excluded):
    for rule in _rules():
        pattern = _promql_regex(rule["expr"], "mountpoint")
        assert (re.fullmatch(pattern, mountpoint) is not None) is excluded


@pytest.mark.parametrize(
    ("fstype", "excluded"),
    [
        ("xfs", False),
        ("ext4", False),
        ("vfat", False),
        ("fuseXlxcfs", False),
        ("tmpfs", True),
        ("squashfs", True),
        ("fuse.lxcfs", True),
        ("fuse.snapfuse", True),
    ],
)
def test_fstype_exclusions_match_pseudo_filesystems_only(fstype, excluded):
    for rule in _rules():
        pattern = _promql_regex(rule["expr"], "fstype")
        assert (re.fullmatch(pattern, fstype) is not None) is excluded
