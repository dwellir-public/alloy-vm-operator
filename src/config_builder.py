#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
"""Config builder for Alloy VM charm."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

DEFAULT_CONFIG_DIR = "/etc/alloy"
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy")
DEFAULT_PACKAGE_CONFIG_BACKUP_PATH = os.path.join(
    DEFAULT_CONFIG_DIR, "config.alloy.package-default"
)
DEFAULT_CONFIG_BACKUP_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy.bak")
REMOTE_WRITE_COMPONENT_NAME = "metrics"
REMOTE_WRITE_MAX_KEEPALIVE = "30m"
DEFAULT_SYSLOG_ACCESS_DROP_EXPRESSIONS = [
    '.*"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE) .* HTTP/.*"',
]


@dataclass(frozen=True)
class ScrapeTarget:
    """One rendered Alloy scrape target."""

    address: str
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricsScrapeJob:
    """A translated subset of a Prometheus scrape job."""

    job_name: str
    targets: list[ScrapeTarget]
    metrics_path: str = "/metrics"
    scheme: str = "http"
    scrape_interval: str = ""
    scrape_timeout: str = ""
    tls_config: dict[str, str | bool] = field(default_factory=dict)


class ConfigBuilder:
    """Alloy configuration builder class."""

    def __init__(
        self,
        *,
        loki_endpoints: list[str],
        remote_write_endpoints: list[str],
        metrics_scrape_jobs: list[MetricsScrapeJob],
        systemd_units: list[str],
        journal_kernel: bool,
        journal_match_expressions: list[str],
        live_debugging: bool = False,
        enable_syslog_receivers: bool = False,
        syslog_drop_access_logs: bool = False,
        syslog_drop_expressions: list[str] | None = None,
        syslog_rate_limit: int = 0,
        syslog_rate_burst: int = 0,
        receiver_hostname: str = "",
        receiver_ip: str = "",
        topology_labels: dict[str, str],
    ):
        self._loki_endpoints = loki_endpoints
        self._remote_write_endpoints = remote_write_endpoints
        self._metrics_scrape_jobs = metrics_scrape_jobs
        self._systemd_units = systemd_units
        self._journal_kernel = journal_kernel
        self._journal_match_expressions = journal_match_expressions
        self._live_debugging = live_debugging
        self._enable_syslog_receivers = enable_syslog_receivers
        self._syslog_drop_access_logs = syslog_drop_access_logs
        self._syslog_drop_expressions = syslog_drop_expressions or []
        self._syslog_rate_limit = syslog_rate_limit
        self._syslog_rate_burst = syslog_rate_burst
        self._receiver_hostname = receiver_hostname
        self._receiver_ip = receiver_ip
        self._topology_labels = topology_labels

    def build(self) -> str:
        """Return the Alloy configuration text."""
        blocks = self._render_base_blocks()
        blocks.extend(self._render_metrics_blocks())
        blocks.extend(self._render_log_blocks())
        return "\n".join(blocks).rstrip() + "\n"

    def _render_base_blocks(self) -> list[str]:
        return [
            self._render_logging(),
            "",
            self._render_unix_exporter(),
            "",
            self._render_local_metrics_relabel(),
            "",
            *([self._render_remote_write(), ""] if self._remote_write_endpoints else []),
            self._render_local_metrics_scrape(),
        ]

    def _render_metrics_blocks(self) -> list[str]:
        if not (self._remote_write_endpoints and self._metrics_scrape_jobs):
            return []
        blocks: list[str] = ["", "// METRICS -> REMOTE WRITE", ""]
        for scrape_job in self._metrics_scrape_jobs:
            blocks.extend([self._render_metrics_scrape(scrape_job), ""])
        return blocks

    def _render_log_blocks(self) -> list[str]:
        blocks: list[str] = []
        if self._live_debugging:
            blocks.extend([self._render_live_debugging(), ""])
        if self._has_log_pipeline():
            blocks.extend(["// LOGS -> LOKI (Juju topology labels)", ""])
        if self._has_journal_sources():
            blocks.extend([self._render_journal_relabel(), ""])
        for block in self._render_service_journal_sources():
            blocks.extend([block, ""])
        for block in self._render_host_journal_sources():
            blocks.extend([block, ""])
        if self._enable_syslog_receivers:
            blocks.extend(
                [
                    self._render_syslog_relabel(),
                    "",
                    *self._render_syslog_processor_blocks(),
                    self._render_syslog_source(),
                    "",
                ]
            )
        if self._has_log_pipeline():
            blocks.extend([self._render_juju_processor(), self._render_loki_writer()])
        return blocks

    def _render_logging(self) -> str:
        return "\n".join(
            [
                "logging {",
                '  level = "warn"',
                "}",
            ]
        )

    def _render_unix_exporter(self) -> str:
        return "\n".join(
            [
                'prometheus.exporter.unix "default" {',
                "  include_exporter_metrics = true",
                '  disable_collectors       = ["mdadm"]',
                "}",
            ]
        )

    def _render_local_metrics_relabel(self) -> str:
        rules = []
        for key in self._topology_label_order():
            value = self._topology_labels.get(key)
            if value:
                rules.extend(
                    [
                        "  rule {",
                        f'    target_label = "{key}"',
                        f"    replacement  = {json.dumps(value)}",
                        "  }",
                        "",
                    ]
                )
        if rules:
            rules.pop()
        return "\n".join(
            [
                'discovery.relabel "local_metrics" {',
                "  targets = array.concat(",
                "    prometheus.exporter.unix.default.targets,",
                "    [{",
                '      job         = "alloy",',
                '      __address__ = "127.0.0.1:6987",',
                "    }],",
                "  )",
                *(rules or [""]),
                "}",
            ]
        )

    def _render_local_metrics_scrape(self) -> str:
        return "\n".join(
            [
                'prometheus.scrape "default" {',
                "  targets    = discovery.relabel.local_metrics.output",
                '  job_name   = "alloy-local"',
                f"  forward_to = {self._metrics_forward_to()}",
                "}",
            ]
        )

    def _render_remote_write(self) -> str:
        endpoint_blocks = "\n".join(
            [
                "\n".join(
                    [
                        "  endpoint {",
                        f'    url = "{endpoint}"',
                        "  }",
                    ]
                )
                for endpoint in self._remote_write_endpoints
            ]
        )
        return "\n".join(
            [
                f'prometheus.remote_write "{REMOTE_WRITE_COMPONENT_NAME}" {{',
                endpoint_blocks,
                "",
                "  wal {",
                f'    max_keepalive_time = "{REMOTE_WRITE_MAX_KEEPALIVE}"',
                "  }",
                "}",
            ]
        )

    def _render_metrics_scrape(self, scrape_job: MetricsScrapeJob) -> str:
        component_name = self._sanitize_component_name(scrape_job.job_name)
        lines = [
            f'prometheus.scrape "{component_name}" {{',
            "  targets = [",
            *self._render_targets(scrape_job.targets),
            "  ]",
            f"  job_name = {json.dumps(scrape_job.job_name)}",
        ]
        if scrape_job.metrics_path:
            lines.append(f"  metrics_path = {json.dumps(scrape_job.metrics_path)}")
        if scrape_job.scheme:
            lines.append(f"  scheme = {json.dumps(scrape_job.scheme)}")
        if scrape_job.scrape_interval:
            lines.append(f"  scrape_interval = {json.dumps(scrape_job.scrape_interval)}")
        if scrape_job.scrape_timeout:
            lines.append(f"  scrape_timeout = {json.dumps(scrape_job.scrape_timeout)}")
        if scrape_job.tls_config:
            lines.extend(self._render_tls_config(scrape_job.tls_config))
        lines.append(f"  forward_to = {self._metrics_forward_to()}")
        lines.append("}")
        return "\n".join(lines)

    def _render_tls_config(self, tls_config: dict[str, str | bool]) -> list[str]:
        lines = ["  tls_config {"]
        for key in sorted(tls_config):
            value = tls_config[key]
            if isinstance(value, bool):
                rendered_value = "true" if value else "false"
            else:
                rendered_value = json.dumps(value)
            lines.append(f"    {self._render_key(key)} = {rendered_value}")
        lines.append("  }")
        return lines

    def _render_targets(self, targets: list[ScrapeTarget]) -> list[str]:
        rendered: list[str] = []
        for target in targets:
            rendered.extend(
                [
                    "    {",
                    f'      __address__ = "{target.address}",',
                    *self._render_target_labels(target.labels),
                    "    },",
                ]
            )
        return rendered

    def _render_target_labels(self, labels: dict[str, str]) -> list[str]:
        lines = []
        for key in sorted(labels):
            lines.append(f"      {self._render_key(key)} = {json.dumps(labels[key])},")
        return lines

    def _render_live_debugging(self) -> str:
        return "\n".join(
            [
                "livedebugging {",
                "  enabled = true",
                "}",
            ]
        )

    def _render_service_journal_sources(self) -> list[str]:
        blocks: list[str] = []
        for index, unit in enumerate(self._systemd_units):
            component_name = "journald" if len(self._systemd_units) == 1 else f"journald_{index}"
            blocks.append(
                "\n".join(
                    [
                        f'loki.source.journal "{component_name}" {{',
                        f'  matches = "{self._format_unit_match(unit)}"',
                        "  relabel_rules = loki.relabel.journal.rules",
                        f'  labels = {{log_source = "journal", systemd_unit = "{unit}"}}',
                        "  forward_to = [loki.process.juju.receiver]",
                        "}",
                    ]
                )
            )
        return blocks

    def _render_host_journal_sources(self) -> list[str]:
        forward_to = (
            "  forward_to = [loki.write.main.receiver]"
            if self._loki_endpoints
            else "  forward_to = []"
        )
        blocks: list[str] = []
        host_matches = self._host_journal_matches()
        for index, match in enumerate(host_matches):
            component_name = (
                "host_journald" if len(host_matches) == 1 else f"host_journald_{index}"
            )
            blocks.append(
                "\n".join(
                    [
                        f'loki.source.journal "{component_name}" {{',
                        f'  matches = "{match}"',
                        "  relabel_rules = loki.relabel.journal.rules",
                        '  labels = {log_source = "journal"}',
                        forward_to,
                        "}",
                    ]
                )
            )
        return blocks

    def _render_journal_relabel(self) -> str:
        return "\n".join(
            [
                'loki.relabel "journal" {',
                "  forward_to = []",
                "",
                "  rule {",
                '    source_labels = ["__journal__systemd_unit"]',
                '    target_label  = "systemd_unit"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__journal_syslog_identifier"]',
                '    target_label  = "syslog_identifier"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__journal_priority_keyword"]',
                '    target_label  = "level"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__journal_priority"]',
                '    target_label  = "severity"',
                "  }",
                "}",
            ]
        )

    def _render_syslog_relabel(self) -> str:
        return "\n".join(
            [
                'loki.relabel "syslog" {',
                "  forward_to = []",
                "",
                "  rule {",
                '    source_labels = ["__syslog_connection_ip_address"]',
                '    target_label  = "source_ip"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_hostname"]',
                '    target_label  = "syslog_hostname"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_connection_hostname"]',
                '    target_label  = "connection_hostname"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_app_name"]',
                '    target_label  = "syslog_app_name"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_facility"]',
                '    target_label  = "syslog_facility"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_proc_id"]',
                '    target_label  = "syslog_proc_id"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_severity"]',
                '    target_label  = "level"',
                "  }",
                "",
                "  rule {",
                '    source_labels = ["__syslog_message_severity"]',
                '    target_label  = "severity"',
                "  }",
                "}",
            ]
        )

    def _render_syslog_source(self) -> str:
        receiver_hostname = self._receiver_hostname or "unknown"
        receiver_ip = self._receiver_ip or "unknown"
        forward_to = (
            "  forward_to = [loki.process.remote_syslog.receiver]"
            if self._loki_endpoints
            else "  forward_to = []"
        )
        return "\n".join(
            [
                'loki.source.syslog "receiver" {',
                "  relabel_rules = loki.relabel.syslog.rules",
                "",
                "  listener {",
                '    address = ":1514"',
                '    labels  = {component = "loki.source.syslog", protocol = "tcp", '
                f'log_source = "remote_syslog", receiver_hostname = "{receiver_hostname}", '
                f'receiver_ip = "{receiver_ip}"' + "}",
                "  }",
                "",
                "  listener {",
                '    address  = ":1514"',
                '    protocol = "udp"',
                '    labels   = {component = "loki.source.syslog", protocol = "udp", '
                f'log_source = "remote_syslog", receiver_hostname = "{receiver_hostname}", '
                f'receiver_ip = "{receiver_ip}"' + "}",
                "  }",
                "",
                forward_to,
                "}",
            ]
        )

    def _render_syslog_processor_blocks(self) -> list[str]:
        if not self._loki_endpoints:
            return []
        return [self._render_remote_syslog_processor(), ""]

    def _render_remote_syslog_processor(self) -> str:
        lines = ['loki.process "remote_syslog" {']
        for expression in self._effective_syslog_drop_expressions():
            lines.extend(
                [
                    "  stage.drop {",
                    f"    expression = {json.dumps(expression)}",
                    "  }",
                    "",
                ]
            )
        if self._should_render_syslog_rate_limit():
            lines.extend(
                [
                    "  stage.limit {",
                    f"    rate = {self._syslog_rate_limit}",
                    f"    burst = {self._effective_syslog_rate_burst()}",
                    "    drop = true",
                    "  }",
                    "",
                ]
            )
        lines.extend(
            [
                "  forward_to = [loki.write.main.receiver]",
                "}",
            ]
        )
        return "\n".join(lines)

    def _render_juju_processor(self) -> str:
        labels_block = "\n".join(self._render_topology_labels())
        forward_to = (
            "  forward_to = [loki.write.main.receiver]"
            if self._loki_endpoints
            else "  forward_to = []"
        )
        return "\n".join(
            [
                'loki.process "juju" {',
                "  stage.static_labels {",
                "    values = {",
                labels_block,
                "    }",
                "  }",
                forward_to,
                "}",
            ]
        )

    def _render_topology_labels(self) -> list[str]:
        lines = []
        for key in self._topology_label_order():
            value = self._topology_labels.get(key)
            if value:
                lines.append(f'      {key} = "{value}",')
        return lines or ["      {}"]

    def _render_loki_writer(self) -> str:
        if not self._loki_endpoints:
            return ""
        endpoints = "\n".join(
            "\n".join(
                [
                    "  endpoint {",
                    f'    url = "{endpoint}"',
                    "  }",
                ]
            )
            for endpoint in self._loki_endpoints
        )
        return "\n".join(
            [
                "",
                'loki.write "main" {',
                endpoints,
                "}",
            ]
        )

    def _metrics_forward_to(self) -> str:
        if not self._remote_write_endpoints:
            return "[]"
        return f"[prometheus.remote_write.{REMOTE_WRITE_COMPONENT_NAME}.receiver]"

    def _has_host_journal_source(self) -> bool:
        return bool(self._host_journal_matches())

    def _has_journal_sources(self) -> bool:
        return bool(self._systemd_units or self._has_host_journal_source())

    def _has_log_pipeline(self) -> bool:
        return bool(
            self._systemd_units or self._has_host_journal_source() or self._enable_syslog_receivers
        )

    def _host_journal_matches(self) -> list[str]:
        matches: list[str] = []
        if self._journal_kernel:
            matches.append("_TRANSPORT=kernel")
        matches.extend(self._journal_match_expressions)
        return matches

    def _effective_syslog_drop_expressions(self) -> list[str]:
        expressions: list[str] = []
        if self._syslog_drop_access_logs:
            expressions.extend(DEFAULT_SYSLOG_ACCESS_DROP_EXPRESSIONS)
        expressions.extend(self._syslog_drop_expressions)
        return expressions

    def _should_render_syslog_rate_limit(self) -> bool:
        return self._syslog_rate_limit > 0

    def _effective_syslog_rate_burst(self) -> int:
        if self._syslog_rate_burst > 0:
            return self._syslog_rate_burst
        return self._syslog_rate_limit

    @staticmethod
    def _sanitize_component_name(name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower()
        return sanitized or "metrics"

    @staticmethod
    def _render_key(key: str) -> str:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            return key
        return json.dumps(key)

    @staticmethod
    def _format_unit_match(value: str) -> str:
        return f"_SYSTEMD_UNIT={value}"

    @staticmethod
    def _topology_label_order() -> list[str]:
        return [
            "juju_model",
            "juju_model_uuid",
            "juju_application",
            "juju_unit",
            "juju_charm",
        ]
