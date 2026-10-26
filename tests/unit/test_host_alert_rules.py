import json
from typing import Any, cast

from alert_rules import validate_rule_groups
from charm import RULE_PUBLICATION_VALUE_LIMIT, AlloyCharm
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


def test_host_filesystem_rules_exclude_pseudo_filesystems():
    for rule in _rules():
        assert (
            'fstype!~"tmpfs|devtmpfs|overlay|squashfs|fuse\\\\.lxcfs|fuse\\\\.snapfuse"'
            in rule["expr"]
        )
        assert 'mountpoint!~"/run.*|/proc.*|/dev.*|/sys.*|/snap/.*|/var/snap/.*"' in rule["expr"]


def test_host_rules_count_against_the_prometheus_publication_budget():
    host_groups = _groups()
    host_bytes = len(json.dumps({"groups": host_groups}, separators=(",", ":")).encode())
    filler = "x" * (RULE_PUBLICATION_VALUE_LIMIT - host_bytes)
    principal_group = {
        "name": "principal",
        "rules": [{"alert": "A", "expr": f"vector(1) > 0 # {filler}"}],
    }
    relation_state = {
        0: {
            "prometheus_alert_rules/one": {
                "backend": "prometheus",
                "ownership": "uuid/app/prometheus_alert_rules/one",
                "groups": [principal_group],
            }
        }
    }

    assert AlloyCharm._rule_state_is_publishable(relation_state)
    assert not AlloyCharm._rule_state_is_publishable(relation_state, host_groups)


def test_host_group_name_collision_is_unpublishable():
    host_groups = _groups()
    relation_state = {
        0: {
            "prometheus_alert_rules/one": {
                "backend": "prometheus",
                "ownership": "uuid/app/prometheus_alert_rules/one",
                "groups": [{"name": host_groups[0]["name"], "rules": []}],
            }
        }
    }

    assert not AlloyCharm._rule_state_is_publishable(relation_state, host_groups)
