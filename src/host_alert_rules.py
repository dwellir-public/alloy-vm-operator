"""Charm-owned host disk and memory usage alert rules for the built-in unix exporter."""

from __future__ import annotations

import json

from config_builder import HOST_METRICS_JOB_LABEL

HOST_ALERT_GROUP_SUFFIX = "host-metrics"
HOST_USAGE_WARNING_PERCENT = 80
HOST_USAGE_CRITICAL_PERCENT = 90
HOST_ALERT_FOR = "5m"
# node_exporter already drops most pseudo filesystems; tmpfs mounts such as
# /run survive its defaults and squashfs snaps are always 100% full.
HOST_DISK_FSTYPE_EXCLUDE = "tmpfs|devtmpfs|overlay|squashfs|iso9660|ramfs"

_DISK_DESCRIPTION = (
    '{{ $labels.mountpoint }} on {{ $labels.juju_unit }} is {{ $value | printf "%.1f" }}% full.'
)
_MEMORY_DESCRIPTION = '{{ $labels.juju_unit }} is using {{ $value | printf "%.1f" }}% of memory.'
_SEVERITIES = (("warning", HOST_USAGE_WARNING_PERCENT), ("critical", HOST_USAGE_CRITICAL_PERCENT))


def _usage_rule(
    alert: str,
    usage_expr: str,
    threshold: int,
    severity: str,
    summary: str,
    description: str,
) -> dict[str, object]:
    """Build one alerting rule that fires when a usage percentage exceeds a threshold."""
    return {
        "alert": alert,
        "expr": f"{usage_expr} > {threshold}",
        "for": HOST_ALERT_FOR,
        # Only severity: the scraped series already carry this charm's Juju
        # topology per unit, and a rule label would override it on every alert.
        "labels": {"severity": severity},
        "annotations": {"summary": summary, "description": description},
    }


def build_host_alert_rule_groups(*, model_uuid: str, application: str) -> list[dict[str, object]]:
    """Return one Prometheus rule group alerting on this application's host usage.

    ``application`` is this charm's own application name: ``discovery.relabel
    "local_metrics"`` stamps the charm's own Juju topology on the exporter
    series, so it scopes the expressions and names the group. Two alloy-vm
    applications in one model therefore never evaluate each other's hosts.
    """
    # The job pins the built-in exporter pipeline. A node-exporter scraped via
    # metrics-endpoint or manual-metrics-jobs carries its own job name and
    # would otherwise raise every alert a second time for the same host.
    base_matcher = (
        f"job={json.dumps(HOST_METRICS_JOB_LABEL)},"
        f"juju_model_uuid={json.dumps(model_uuid)},"
        f"juju_application={json.dumps(application)}"
    )
    disk_matcher = f"{base_matcher},fstype!~{json.dumps(HOST_DISK_FSTYPE_EXCLUDE)}"
    disk_usage = (
        f"100 * (1 - node_filesystem_avail_bytes{{{disk_matcher}}}"
        f" / node_filesystem_size_bytes{{{disk_matcher}}})"
    )
    memory_usage = (
        f"100 * (1 - node_memory_MemAvailable_bytes{{{base_matcher}}}"
        f" / node_memory_MemTotal_bytes{{{base_matcher}}})"
    )
    rules: list[dict[str, object]] = []
    for severity, threshold in _SEVERITIES:
        rules.append(
            _usage_rule(
                f"HostDiskUsage{severity.capitalize()}",
                disk_usage,
                threshold,
                severity,
                f"Disk usage above {threshold}% on {{{{ $labels.juju_unit }}}}"
                " ({{ $labels.mountpoint }})",
                _DISK_DESCRIPTION,
            )
        )
    for severity, threshold in _SEVERITIES:
        rules.append(
            _usage_rule(
                f"HostMemoryUsage{severity.capitalize()}",
                memory_usage,
                threshold,
                severity,
                f"Memory usage above {threshold}% on {{{{ $labels.juju_unit }}}}",
                _MEMORY_DESCRIPTION,
            )
        )
    # Receivers such as mimir-vm merge every relation into one ruler namespace and
    # reject duplicate group names, so the name must be unique across models and
    # across alloy-vm applications in one model.
    return [{"name": f"{model_uuid}-{application}-{HOST_ALERT_GROUP_SUFFIX}", "rules": rules}]
