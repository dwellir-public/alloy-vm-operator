"""Built-in Prometheus alert rules for host metrics that Alloy collects itself."""

from __future__ import annotations

import json

HOST_FILESYSTEM_GROUP_SUFFIX = "host-filesystem"

# Pseudo and per-boot filesystems never hold node data, so alerts on them are noise.
_FSTYPE_EXCLUDE = "tmpfs|devtmpfs|overlay|squashfs|fuse\\\\.lxcfs|fuse\\\\.snapfuse"
_MOUNTPOINT_EXCLUDE = "/(run|proc|dev|sys|snap)(/.*)?"
_RUNBOOK_BASE = "https://docs.internal.dwellir.com/docs/operations/grafana_alert_runbooks"
_DISK_SPACE_RUNBOOK = f"{_RUNBOOK_BASE}/gra_0019_host_disk_space"
_READ_ONLY_RUNBOOK = f"{_RUNBOOK_BASE}/gra_0020_host_filesystem_read_only"
_DEVICE_ERROR_RUNBOOK = f"{_RUNBOOK_BASE}/gra_0021_host_filesystem_device_error"


def host_filesystem_rule_groups(
    *, model: str, model_uuid: str, application: str, charm_name: str
) -> list[dict[str, object]]:
    """Return filesystem alert groups scoped to this Alloy application's own host metrics."""
    topology = {
        "juju_model": model,
        "juju_model_uuid": model_uuid,
        "juju_application": application,
    }
    selector = ",".join(
        [
            *(f"{key}={json.dumps(value)}" for key, value in topology.items()),
            f'fstype!~"{_FSTYPE_EXCLUDE}"',
            f'mountpoint!~"{_MOUNTPOINT_EXCLUDE}"',
        ]
    )
    used_percent = (
        f"100 * (1 - node_filesystem_avail_bytes{{{selector}}}"
        f" / node_filesystem_size_bytes{{{selector}}})"
    )
    labels = {**topology, "juju_charm": charm_name}
    mount = "'{{ $labels.mountpoint }}'"
    where = (
        "on {{ $labels.instance }} "
        "(unit {{ $labels.juju_unit }} in model {{ $labels.juju_model }})"
    )
    rules: list[dict[str, object]] = [
        {
            "alert": "HostDiskSpaceWarning",
            "expr": f"{used_percent} > 80",
            "for": "30m",
            "labels": {**labels, "severity": "warning"},
            "annotations": {
                "summary": f"[GRA-0019] Filesystem {mount} is over 80% full {where}",
                "runbook_url": _DISK_SPACE_RUNBOOK,
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) uses "
                    '{{ $value | printf "%.1f" }}% of its space.'
                ),
            },
        },
        {
            "alert": "HostDiskSpace",
            "expr": f"{used_percent} > 90",
            "for": "15m",
            "labels": {**labels, "severity": "critical"},
            "annotations": {
                "summary": f"[GRA-0019] Filesystem {mount} is over 90% full {where}",
                "runbook_url": _DISK_SPACE_RUNBOOK,
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) uses "
                    '{{ $value | printf "%.1f" }}% of its space.'
                ),
            },
        },
        {
            "alert": "HostFilesystemReadOnly",
            "expr": f"node_filesystem_readonly{{{selector}}} == 1",
            "for": "5m",
            "labels": {**labels, "severity": "critical"},
            "annotations": {
                "summary": f"[GRA-0020] Filesystem {mount} is read-only {where}",
                "runbook_url": _READ_ONLY_RUNBOOK,
                "description": (
                    "Filesystem '{{ $labels.mountpoint }}' ({{ $labels.device }}) is mounted "
                    "read-only. The kernel may have remounted it after an I/O or filesystem error."
                ),
            },
        },
        {
            "alert": "HostFilesystemDeviceError",
            # Another readable mount of the same device still reports its space.
            "expr": (
                f"node_filesystem_device_error{{{selector}}} == 1"
                f" unless on (instance, device) node_filesystem_device_error{{{selector}}} == 0"
            ),
            "for": "15m",
            "labels": {**labels, "severity": "warning"},
            "annotations": {
                "summary": f"[GRA-0021] Alloy cannot read filesystem {mount} {where}",
                "runbook_url": _DEVICE_ERROR_RUNBOOK,
                "description": (
                    "statfs failed for '{{ $labels.mountpoint }}' ({{ $labels.device }}), and "
                    "no other mount of that device is readable. Space alerts for the device "
                    "are blind until Alloy can read it."
                ),
            },
        },
    ]
    return [
        {
            "name": f"{model_uuid}-{application}-{HOST_FILESYSTEM_GROUP_SUFFIX}",
            "rules": rules,
        }
    ]
