#!/usr/bin/env python3
# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
"""Charm the application."""

import logging
import socket
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

import ops
from charms.loki_k8s.v1.loki_push_api import LokiPushApiConsumer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointConsumer
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteConsumer
from cosl import JujuTopology

import alloy
from config_builder import (
    DEFAULT_CONFIG_BACKUP_PATH,
    DEFAULT_CONFIG_PATH,
    DEFAULT_PACKAGE_CONFIG_BACKUP_PATH,
    ConfigBuilder,
    MetricsScrapeJob,
    ScrapeTarget,
)

logger = logging.getLogger(__name__)


class AlloyCharm(ops.CharmBase):
    """Machine charm for Grafana Alloy."""

    _stored = ops.StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self._stored.set_default(
            last_good_config="",
            last_failed_config_path="",
            config_drifted=False,
            last_custom_args="",
            last_live_debugging=False,
            last_syslog_receivers_enabled=False,
            livedebug_prev_custom_args="",
            livedebug_prev_custom_args_set=False,
        )
        self._topology = JujuTopology.from_charm(self)
        self._loki_consumer = LokiPushApiConsumer(
            self,
            relation_name="send-loki-logs",
            forward_alert_rules=False,
            skip_alert_topology_labeling=True,
        )
        self._metrics_consumer = MetricsEndpointConsumer(
            self,
            relation_name="metrics-endpoint",
        )
        self._remote_write_consumer = PrometheusRemoteWriteConsumer(
            self,
            relation_name="send-remote-write",
            peer_relation_name="alloy-peers",
            forward_alert_rules=False,
        )
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(
            self._loki_consumer.on.loki_push_api_endpoint_joined,
            self._on_loki_endpoint_changed,
        )
        self.framework.observe(
            self._loki_consumer.on.loki_push_api_endpoint_departed,
            self._on_loki_endpoint_changed,
        )
        self.framework.observe(
            self._metrics_consumer.on.targets_changed,
            self._on_metrics_relation_changed,
        )
        self.framework.observe(
            self._remote_write_consumer.on.endpoints_changed,
            self._on_metrics_relation_changed,
        )

    def _on_install(self, event):
        self.unit.status = ops.MaintenanceStatus("Installing Alloy")
        try:
            alloy.install()
            alloy.preserve_default_config(
                config_path=Path(DEFAULT_CONFIG_PATH),
                preserved_path=Path(DEFAULT_PACKAGE_CONFIG_BACKUP_PATH),
            )
        except subprocess.CalledProcessError as exc:
            self.unit.status = ops.MaintenanceStatus(f"Installation failed: {exc}")
            event.defer()

    def _on_start(self, event):
        self.unit.status = ops.MaintenanceStatus("Starting Alloy")
        try:
            alloy.start()
        except subprocess.CalledProcessError as exc:
            self.unit.status = ops.MaintenanceStatus(f"Failed to start Alloy: {exc}")
            event.defer()
            return
        version = alloy.get_version()
        if version is not None:
            self.unit.set_workload_version(version)
        self.unit.status = ops.ActiveStatus("Alloy is running")

    def _on_config_changed(self, event):
        self.unit.status = ops.MaintenanceStatus("Configuring Alloy")
        if self._configure():
            self.unit.status = self._post_config_status("Alloy config updated and valid")
        else:
            self.unit.status = ops.MaintenanceStatus(
                "Invalid Alloy config. No changes applied."
            )

    def _on_upgrade_charm(self, event):
        """Handle charm upgrade without restarting or rewriting configuration."""
        logger.info("Upgrade-charm event: skipping config rewrite and restart.")

    def _on_loki_endpoint_changed(self, event):
        """Update config when Loki endpoints change."""
        self.unit.status = ops.MaintenanceStatus("Updating Loki endpoints")
        if self._configure():
            self.unit.status = self._post_config_status("Alloy config updated and valid")
        else:
            self.unit.status = ops.MaintenanceStatus(
                "Invalid Alloy config. No changes applied."
            )

    def _on_metrics_relation_changed(self, event):
        """Update config when metrics scrape or remote write endpoints change."""
        self.unit.status = ops.MaintenanceStatus("Updating metrics endpoints")
        if self._configure():
            self.unit.status = self._post_config_status("Alloy config updated and valid")
        else:
            self.unit.status = ops.MaintenanceStatus(
                "Invalid Alloy config. No changes applied."
            )

    def _on_update_status(self, event):
        """Handle periodic status updates (detect drift and workload health)."""
        if not alloy.is_active():
            self.unit.status = ops.MaintenanceStatus("Alloy service not running")
            return
        version = alloy.get_version()
        if version is not None:
            self.unit.set_workload_version(version)
        self._reconcile_config_drift_status()
        if self._is_service_down_status() and not self._stored.config_drifted:
            self.unit.status = self._post_config_status("Alloy is running.")

    def _configure(self) -> bool:
        """Render, validate, and persist the Alloy configuration.

        Flow:
        - Render config text from `config-override` or the ConfigBuilder.
        - Short-circuit if config and /etc/default/alloy args are unchanged.
        - Validate config using `alloy fmt` against a temp file.
        - Persist config + backup, update /etc/default/alloy, restart/reload.
        - Track last-good config and clear drift status on success.
        """
        alloy.ensure_config_dir_permissions(str(Path(DEFAULT_CONFIG_PATH).parent))
        config_text = self._render_config_text()
        desired_custom_args = self._desired_custom_args()
        live_debugging = self._live_debugging_enabled()
        syslog_receivers_enabled = self._syslog_receivers_enabled()
        if not config_text:
            return True
        if (
            self._stored.last_good_config
            and config_text == self._stored.last_good_config
            and not self._stored.config_drifted
            and desired_custom_args == self._stored.last_custom_args
            and live_debugging == self._stored.last_live_debugging
            and syslog_receivers_enabled == self._stored.last_syslog_receivers_enabled
        ):
            logger.debug("Alloy config unchanged; skipping apply.")
            return True
        if not self._validate_config_text(config_text):
            logger.warning("Configuration validation failed; keeping previous config.")
            failed_path = self._persist_failed_config(config_text)
            if failed_path:
                self._stored.last_failed_config_path = str(failed_path)
            return False
        alloy.write_config_text(
            config_text,
            config_path=Path(DEFAULT_CONFIG_PATH),
            backup_path=Path(DEFAULT_CONFIG_BACKUP_PATH),
        )
        self._stored.last_good_config = config_text
        if self._stored.config_drifted:
            logger.info("Alloy config drift resolved by applying charm configuration.")
        self._stored.config_drifted = False
        self._write_alloy_systemd_unit_defaults()
        self._apply_live_debugging(baseline_custom_args=desired_custom_args)
        self._stored.last_custom_args = desired_custom_args
        self._stored.last_live_debugging = live_debugging
        self._stored.last_syslog_receivers_enabled = syslog_receivers_enabled
        try:
            # Restart ensures the new config is picked up even if reload support is flaky.
            # Reload then avoids the service settling with stale runtime state.
            alloy.restart()
            alloy.reload()
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to restart/reload Alloy after config update: %s", exc)
            return False
        return True

    def _reconcile_config_drift_status(self) -> None:
        """Detect config drift and update unit status if appropriate."""
        if not self._stored.last_good_config:
            if self._seed_last_good_config_from_disk():
                return
        drifted = self._has_config_drift()
        if drifted != self._stored.config_drifted:
            if drifted:
                logger.warning(
                    "Detected manual Alloy config change at %s", DEFAULT_CONFIG_PATH
                )
            else:
                logger.info("Alloy config drift cleared.")
        self._stored.config_drifted = drifted
        if drifted:
            if isinstance(self.unit.status, (ops.ActiveStatus, ops.MaintenanceStatus)):
                self.unit.status = ops.MaintenanceStatus(self._config_drift_message())
            return
        if self._is_drift_status():
            self.unit.status = ops.ActiveStatus()

    def _seed_last_good_config_from_disk(self) -> bool:
        """Populate last_good_config from disk when it is still unset."""
        on_disk = self._read_config_from_disk()
        if on_disk is None:
            return False
        self._stored.last_good_config = on_disk
        self._stored.config_drifted = False
        logger.debug(
            "Seeded last_good_config from %s as part of initial reconcile",
            DEFAULT_CONFIG_PATH,
        )
        return True

    def _has_config_drift(self) -> bool:
        """Return True when the on-disk config differs from the last good config."""
        if not self._stored.last_good_config:
            return False
        on_disk = self._read_config_from_disk()
        if on_disk is None:
            return True
        return on_disk != self._stored.last_good_config

    def _read_config_from_disk(self) -> str | None:
        """Read the on-disk config text, returning None if unreadable."""
        path = Path(DEFAULT_CONFIG_PATH)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Failed to read Alloy config from disk: %s", exc)
            return None

    def _config_drift_message(self) -> str:
        return f"Manual Alloy config change detected at {DEFAULT_CONFIG_PATH}"

    def _is_drift_status(self) -> bool:
        return (
            isinstance(self.unit.status, ops.MaintenanceStatus)
            and self.unit.status.message == self._config_drift_message()
        )

    def _is_service_down_status(self) -> bool:
        return (
            isinstance(self.unit.status, ops.MaintenanceStatus)
            and self.unit.status.message == "Alloy service not running"
        )

    def _render_config_text(self) -> str:
        override = str(self.config.get("config-override", "")).strip()
        if override:
            return override
        builder = ConfigBuilder(
            loki_endpoints=self._loki_endpoint_urls(),
            remote_write_endpoints=self._remote_write_endpoint_urls(),
            metrics_scrape_jobs=self._active_metrics_scrape_jobs(),
            systemd_units=self._systemd_units(),
            live_debugging=self._live_debugging_enabled(),
            enable_syslog_receivers=self._syslog_receivers_enabled(),
            receiver_hostname=self._syslog_receiver_hostname(),
            receiver_ip=self._syslog_receiver_ip(),
            topology_labels=self._topology.as_dict(
                remapped_keys={
                    "model": "juju_model",
                    "model_uuid": "juju_model_uuid",
                    "application": "juju_application",
                    "unit": "juju_unit",
                    "charm_name": "juju_charm",
                }
            ),
        )
        return f"{alloy.GENERATED_CONFIG_HEADER}{builder.build()}"

    def _validate_config_text(self, config_text: str) -> bool:
        """Validate Alloy config by writing to a temp file and running verify."""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
                tmp.write(config_text)
                tmp_path = Path(tmp.name)
            alloy.verify_config(config_path=tmp_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Alloy config verification failed: %s", exc)
            return False
        finally:
            if tmp_path and tmp_path.exists():
                tmp_path.unlink()
        return True

    def _persist_failed_config(self, config_text: str) -> Path | None:
        """Persist a failed config to /tmp for debugging."""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                delete=False,
                prefix="alloy-config-invalid-",
                suffix=".yaml",
                dir="/tmp",
            ) as tmp:
                tmp.write(config_text)
                failed_path = Path(tmp.name)
            logger.warning("Invalid Alloy config kept at %s", failed_path)
            return failed_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist invalid config: %s", exc)
            return None

    def _write_alloy_systemd_unit_defaults(self) -> None:
        """Write /etc/default/alloy based on charm config."""
        custom_args = self._desired_custom_args()
        with open("/etc/default/alloy", "w") as handle:
            handle.write(f'CUSTOM_ARGS="{custom_args}"\n')
            handle.write(f'CONFIG_FILE="{DEFAULT_CONFIG_PATH}"\n')

    def _desired_custom_args(self) -> str:
        return str(self.config.get("custom_args", "--server.http.listen-addr=0.0.0.0:6987"))

    def _apply_live_debugging(self, *, baseline_custom_args: str) -> None:
        """Toggle live debugging by updating CUSTOM_ARGS, preserving prior value."""
        enabled = self._live_debugging_enabled()
        if enabled:
            if not self._stored.livedebug_prev_custom_args_set:
                previous = alloy.read_custom_args()
                self._stored.livedebug_prev_custom_args = previous or baseline_custom_args
                self._stored.livedebug_prev_custom_args_set = True
            else:
                self._stored.livedebug_prev_custom_args = baseline_custom_args
            alloy.write_custom_args("--server.http.listen-addr=0.0.0.0:12345")
            return
        if self._stored.livedebug_prev_custom_args_set:
            previous = self._stored.livedebug_prev_custom_args or baseline_custom_args
            alloy.write_custom_args(previous or None)
            self._stored.livedebug_prev_custom_args = ""
            self._stored.livedebug_prev_custom_args_set = False

    def _live_debugging_enabled(self) -> bool:
        """Return True when live debugging is enabled in config."""
        return bool(self.config.get("alloy-livedebugging", False))

    def _syslog_receivers_enabled(self) -> bool:
        """Return True when syslog receiver listeners should be enabled."""
        return bool(self.config.get("enable-syslogreceivers", False))

    def _syslog_receiver_hostname(self) -> str:
        """Return hostname label for syslog receiver listeners."""
        try:
            return socket.getfqdn()
        except OSError:
            return self.unit.name

    def _syslog_receiver_ip(self) -> str:
        """Return ingress IP label for syslog receiver listeners."""
        for endpoint in ("syslog-receiver", ""):
            try:
                binding = self.model.get_binding(endpoint)
            except (ops.ModelError, KeyError):
                continue
            if binding and binding.network.ingress_address:
                return str(binding.network.ingress_address)
        return "0.0.0.0"

    def _loki_endpoint_urls(self) -> list[str]:
        return [
            endpoint["url"]
            for endpoint in self._loki_consumer.loki_endpoints
            if endpoint.get("url")
        ]

    def _remote_write_endpoint_urls(self) -> list[str]:
        return [
            endpoint["url"]
            for endpoint in self._remote_write_consumer.endpoints
            if endpoint.get("url")
        ]

    def _active_metrics_scrape_jobs(self) -> list[MetricsScrapeJob]:
        """Return translated remote scrape jobs only when an upstream exists."""
        if not self._remote_write_endpoint_urls():
            return []
        return self._translated_metrics_scrape_jobs()

    def _translated_metrics_scrape_jobs(self) -> list[MetricsScrapeJob]:
        """Translate a supported subset of Prometheus scrape jobs into Alloy jobs."""
        translated: list[MetricsScrapeJob] = []
        for job in self._metrics_consumer.jobs():
            translated_job = self._translate_metrics_scrape_job(job)
            if translated_job is not None:
                translated.append(translated_job)
        return translated

    def _translate_metrics_scrape_job(
        self,
        job: Mapping[str, object],
    ) -> MetricsScrapeJob | None:
        """Translate one supported Prometheus scrape job into an Alloy scrape job."""
        unsupported_job_keys = set(job) - {
            "job_name",
            "metrics_path",
            "scheme",
            "scrape_interval",
            "scrape_timeout",
            "static_configs",
            "relabel_configs",
        }
        if unsupported_job_keys:
            logger.warning(
                "Skipping unsupported scrape job %s with fields %s",
                job.get("job_name", "<unnamed>"),
                sorted(unsupported_job_keys),
            )
            return None

        raw_static_configs = job.get("static_configs", [])
        if not isinstance(raw_static_configs, list):
            logger.warning(
                "Skipping scrape job with invalid static_configs: %r",
                raw_static_configs,
            )
            return None

        scrape_targets = self._translate_static_configs(job, raw_static_configs)
        if not scrape_targets:
            logger.warning(
                "Skipping scrape job %s due to invalid or empty targets",
                job.get("job_name", "<unnamed>"),
            )
            return None

        return MetricsScrapeJob(
            job_name=str(job.get("job_name", "metrics-endpoint")),
            targets=scrape_targets,
            metrics_path=str(job.get("metrics_path", "/metrics")),
            scheme=str(job.get("scheme", "http")),
            scrape_interval=str(job.get("scrape_interval", "")),
            scrape_timeout=str(job.get("scrape_timeout", "")),
        )

    def _translate_static_configs(
        self,
        job: Mapping[str, object],
        raw_static_configs: list[object],
    ) -> list[ScrapeTarget]:
        """Translate static_configs into Alloy targets for one scrape job."""
        scrape_targets: list[ScrapeTarget] = []
        for static_config in raw_static_configs:
            if not isinstance(static_config, Mapping):
                return []
            unsupported_static_keys = set(static_config) - {"targets", "labels"}
            if unsupported_static_keys:
                logger.warning(
                    "Skipping unsupported scrape job %s static config fields %s",
                    job.get("job_name", "<unnamed>"),
                    sorted(unsupported_static_keys),
                )
                return []
            labels = static_config.get("labels", {})
            if labels is None:
                labels = {}
            if not isinstance(labels, Mapping):
                return []
            targets = static_config.get("targets", [])
            if not isinstance(targets, list):
                return []
            for target in targets:
                if not isinstance(target, str):
                    return []
                scrape_targets.append(
                    ScrapeTarget(
                        address=self._normalize_scrape_target(target),
                        labels={str(key): str(value) for key, value in labels.items()},
                    )
                )
        return scrape_targets

    @staticmethod
    def _normalize_scrape_target(target: str) -> str:
        """Normalize a scrape target, bracketing IPv6 host literals when needed."""
        target = target.strip()
        if not target or target.startswith("["):
            return target
        if target.count(":") <= 1:
            return target
        host, _, port = target.rpartition(":")
        if host and port.isdigit():
            try:
                resolved_host = socket.gethostbyaddr(host)[0]
            except (socket.herror, OSError):
                resolved_host = ""
            if resolved_host:
                return f"{resolved_host}:{port}"
            return f"[{host}]:{port}"
        return f"[{target}]"

    def _post_config_status(self, active_message: str) -> ops.StatusBase:
        """Return the desired post-config status for the current relation state."""
        if self._metrics_consumer.jobs() and not self._remote_write_endpoint_urls():
            return ops.WaitingStatus(
                "Waiting for remote write before enabling related metrics scraping"
            )
        return ops.ActiveStatus(active_message)

    def _systemd_units(self) -> list[str]:
        raw = str(self.config.get("systemd-units", "")).strip()
        if not raw:
            return []
        tokens = raw.replace("\n", ",").split(",")
        return [token.strip() for token in tokens if token.strip()]


if __name__ == "__main__":
    ops.main(AlloyCharm)
