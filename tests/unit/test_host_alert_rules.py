from typing import Any, cast

from alert_rules import validate_rule_groups
from config_builder import HOST_METRICS_JOB_LABEL
from host_alert_rules import build_host_alert_rule_groups

MODEL_UUID = "00000000-0000-4000-8000-000000000111"
# The series carry this charm's own topology from discovery.relabel "local_metrics",
# so the selectors scope on the model UUID and application name of alloy-vm itself.
# Alloy's exporter framework stamps job="integrations/unix" on the exporter targets and
# prometheus.scrape's job_name never overrides an existing job label, so that is the value
# to match; it also keeps node-exporters scraped via metrics-endpoint from alerting twice.
BASE_MATCHER = (
    f'job="integrations/unix",juju_model_uuid="{MODEL_UUID}",juju_application="alloy-vm"'
)
DISK_MATCHER = f'{BASE_MATCHER},fstype!~"tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"'


def _rules() -> list[dict[str, Any]]:
    groups = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-vm")
    assert len(groups) == 1
    return cast(list[dict[str, Any]], groups[0]["rules"])


def test_build_returns_one_group_named_after_the_model_and_application():
    """Receivers merge every relation into one namespace, so the name must be unique."""
    groups = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-vm")
    assert [group["name"] for group in groups] == [f"{MODEL_UUID}-alloy-vm-host-metrics"]
    assert set(groups[0]) == {"name", "rules"}


def test_rules_are_ordered_disk_then_memory_warning_then_critical():
    assert [rule["alert"] for rule in _rules()] == [
        "HostDiskUsageWarning",
        "HostDiskUsageCritical",
        "HostMemoryUsageWarning",
        "HostMemoryUsageCritical",
    ]


def test_disk_rules_measure_used_fraction_of_real_filesystems():
    disk_warning, disk_critical, _, _ = _rules()
    usage = (
        f"100 * (1 - node_filesystem_avail_bytes{{{DISK_MATCHER}}}"
        f" / node_filesystem_size_bytes{{{DISK_MATCHER}}})"
    )
    assert disk_warning["expr"] == f"{usage} > 80"
    assert disk_critical["expr"] == f"{usage} > 90"


def test_memory_rules_measure_available_over_total():
    _, _, memory_warning, memory_critical = _rules()
    usage = (
        f"100 * (1 - node_memory_MemAvailable_bytes{{{BASE_MATCHER}}}"
        f" / node_memory_MemTotal_bytes{{{BASE_MATCHER}}})"
    )
    assert memory_warning["expr"] == f"{usage} > 80"
    assert memory_critical["expr"] == f"{usage} > 90"


def test_every_rule_waits_five_minutes_and_carries_only_a_severity_label():
    rules = _rules()
    assert [rule["for"] for rule in rules] == ["5m"] * 4
    assert [rule["labels"] for rule in rules] == [
        {"severity": "warning"},
        {"severity": "critical"},
        {"severity": "warning"},
        {"severity": "critical"},
    ]


def test_every_selector_is_restricted_to_the_built_in_host_metrics_job():
    assert HOST_METRICS_JOB_LABEL == "integrations/unix"
    for rule in _rules():
        assert rule["expr"].count(f'job="{HOST_METRICS_JOB_LABEL}"') == 2, rule["expr"]


def test_rules_do_not_set_topology_labels():
    for rule in _rules():
        assert not any(key.startswith("juju_") for key in rule["labels"])


def test_annotations_name_the_unit_mountpoint_and_value():
    rules = _rules()
    assert rules[0]["annotations"] == {
        "summary": "Disk usage above 80% on {{ $labels.juju_unit }} ({{ $labels.mountpoint }})",
        "description": (
            "{{ $labels.mountpoint }} on {{ $labels.juju_unit }} is "
            '{{ $value | printf "%.1f" }}% full.'
        ),
    }
    assert rules[3]["annotations"] == {
        "summary": "Memory usage above 90% on {{ $labels.juju_unit }}",
        "description": '{{ $labels.juju_unit }} is using {{ $value | printf "%.1f" }}% of memory.',
    }


def test_model_uuid_and_application_are_json_escaped_in_matchers():
    groups = build_host_alert_rule_groups(model_uuid='a"b\\c', application='p"q')
    expr = cast(list[dict[str, Any]], groups[0]["rules"])[0]["expr"]
    assert 'juju_model_uuid="a\\"b\\\\c",juju_application="p\\"q"' in expr


def test_group_name_and_selectors_follow_the_application():
    first = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-vm")[0]
    second = build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-vm-b")[0]
    assert first["name"] != second["name"]
    first_rules = cast(list[dict[str, Any]], first["rules"])
    second_rules = cast(list[dict[str, Any]], second["rules"])
    assert 'juju_application="alloy-vm"' in first_rules[0]["expr"]
    assert 'juju_application="alloy-vm-b"' in second_rules[0]["expr"]


def test_groups_pass_the_backend_rule_schema():
    assert validate_rule_groups(
        build_host_alert_rule_groups(model_uuid=MODEL_UUID, application="alloy-vm")
    )
