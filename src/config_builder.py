#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
"""Config builder for Alloy VM charm."""

import os

DEFAULT_CONFIG_DIR = "/etc/alloy"
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy")
DEFAULT_PACKAGE_CONFIG_BACKUP_PATH = os.path.join(
    DEFAULT_CONFIG_DIR, "config.alloy.package-default"
)
DEFAULT_CONFIG_BACKUP_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.alloy.bak")

DEFAULT_CONFIG_CONTENT = """
logging {
  level = "warn"
}

prometheus.exporter.unix "default" {
  include_exporter_metrics = true
  disable_collectors       = ["mdadm"]
}

prometheus.scrape "default" {
  targets = array.concat(
    prometheus.exporter.unix.default.targets,
    [{
      // Self-collect metrics
      job         = "alloy",
      __address__ = "127.0.0.1:6987",
    }],
  )

  forward_to = []
}
""".lstrip()


class ConfigBuilder:
    """Alloy configuration builder class."""

    def __init__(
        self,
        *,
        loki_endpoints: list[str],
        systemd_units: list[str],
        live_debugging: bool = False,
        enable_syslog_receivers: bool = False,
        receiver_hostname: str = "",
        receiver_ip: str = "",
        topology_labels: dict[str, str],
    ):
        self._loki_endpoints = loki_endpoints
        self._systemd_units = systemd_units
        self._live_debugging = live_debugging
        self._enable_syslog_receivers = enable_syslog_receivers
        self._receiver_hostname = receiver_hostname
        self._receiver_ip = receiver_ip
        self._topology_labels = topology_labels

    def build(self) -> str:
        """Return the Alloy configuration text."""
        base = DEFAULT_CONFIG_CONTENT.rstrip()
        if (
            not self._systemd_units
            and not self._live_debugging
            and not self._enable_syslog_receivers
        ):
            return f"{base}\n"

        blocks: list[str] = [base, ""]
        if self._live_debugging:
            blocks.append(self._render_live_debugging())
            blocks.append("")
        if self._systemd_units or self._enable_syslog_receivers:
            blocks.extend(["// LOGS -> LOKI (Juju topology labels)", ""])
        if self._systemd_units:
            blocks.extend([self._render_journal_source(), ""])
        if self._enable_syslog_receivers:
            blocks.extend(
                [
                    self._render_syslog_relabel(),
                    "",
                    self._render_syslog_source(),
                    "",
                ]
            )
        blocks.extend([self._render_juju_processor(), self._render_loki_writer()])
        return "\n".join(blocks).rstrip() + "\n"

    def _render_live_debugging(self) -> str:
        return "\n".join(
            [
                "livedebugging {",
                "  enabled = true",
                "}",
            ]
        )

    def _render_journal_source(self) -> str:
        matches = self._format_matches(self._systemd_units)
        return "\n".join(
            [
                'loki.source.journal "journald" {',
                f'  matches = "{matches}"',
                "  forward_to = [loki.process.juju.receiver]",
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
            "  forward_to = [loki.write.main.receiver]"
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
                "    labels  = {component = \"loki.source.syslog\", protocol = \"tcp\", "
                f'log_source = "remote_syslog", receiver_hostname = "{receiver_hostname}", '
                f'receiver_ip = "{receiver_ip}"' + "}",
                "  }",
                "",
                "  listener {",
                '    address  = ":1514"',
                '    protocol = "udp"',
                "    labels   = {component = \"loki.source.syslog\", protocol = \"udp\", "
                f'log_source = "remote_syslog", receiver_hostname = "{receiver_hostname}", '
                f'receiver_ip = "{receiver_ip}"' + "}",
                "  }",
                "",
                forward_to,
                "}",
            ]
        )

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

    @staticmethod
    def _format_matches(values: list[str]) -> str:
        clauses = [f"_SYSTEMD_UNIT={value}" for value in values]
        return " OR ".join(clauses)

    @staticmethod
    def _topology_label_order() -> list[str]:
        return [
            "juju_model",
            "juju_model_uuid",
            "juju_application",
            "juju_unit",
            "juju_charm",
        ]
