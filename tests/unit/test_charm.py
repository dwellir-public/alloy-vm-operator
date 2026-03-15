# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

from dataclasses import replace

from ops import testing

from charm import AlloyCharm

DEFAULT_ARGS = "--server.http.listen-addr=0.0.0.0:6987"


def test_start(monkeypatch):
    # Arrange:
    ctx = testing.Context(AlloyCharm)
    monkeypatch.setattr("charm.alloy.start", lambda: None)
    monkeypatch.setattr("charm.alloy.get_version", lambda: "1.0.0")
    # Act:
    state_out = ctx.run(ctx.on.start(), testing.State())
    # Assert:
    assert state_out.workload_version is not None
    assert state_out.unit_status == testing.ActiveStatus("Alloy is running")


def test_config_drift_sets_maintenance(monkeypatch, tmp_path):
    ctx = testing.Context(AlloyCharm)
    config_path = tmp_path / "config.alloy"
    monkeypatch.setattr("charm.DEFAULT_CONFIG_PATH", str(config_path))
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)

    def write_config_text(config_text: str, *, config_path, **_):
        config_path.write_text(config_text, encoding="utf-8")

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "logging { level = \"warn\" }\n",
        "custom_args": "--server.http.listen-addr=0.0.0.0:6987",
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "log-level": "info",
    }

    state_out = ctx.run(ctx.on.config_changed(), testing.State(config=config))
    assert isinstance(state_out.unit_status, testing.ActiveStatus)

    config_path.write_text("manual: true\n", encoding="utf-8")
    state_out = ctx.run(ctx.on.update_status(), state_out)

    assert isinstance(state_out.unit_status, testing.MaintenanceStatus)


def test_update_status_refreshes_workload_version(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    monkeypatch.setattr("charm.alloy.is_active", lambda: True)
    monkeypatch.setattr("charm.alloy.get_version", lambda: "1.12.2")
    monkeypatch.setattr("charm.AlloyCharm._reconcile_config_drift_status", lambda *_: None)

    state_out = ctx.run(ctx.on.update_status(), testing.State())

    assert state_out.workload_version == "1.12.2"


def test_live_debugging_enabled_writes_debug_args_and_config(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    written_args = []
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda value: written_args.append(value))
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": True,
        "enable-syslogreceivers": False,
        "systemd-units": "ssh.service",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    assert "livedebugging {" in seen["config"]
    assert "enabled = true" in seen["config"]
    assert written_args[-1] == "--server.http.listen-addr=0.0.0.0:12345"


def test_live_debugging_disable_restores_previous_args(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    written_args = []
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda value: written_args.append(value))
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.write_config_text", lambda *_, **__: None)

    enabled = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": True,
        "enable-syslogreceivers": False,
        "systemd-units": "ssh.service",
        "log-level": "info",
    }
    disabled = {**enabled, "alloy-livedebugging": False}

    state = ctx.run(ctx.on.config_changed(), testing.State(config=enabled))
    ctx.run(ctx.on.config_changed(), replace(state, config=disabled))

    assert written_args[0] == "--server.http.listen-addr=0.0.0.0:12345"
    assert written_args[-1] == "--server.http.listen-addr=0.0.0.0:6987"


def test_syslog_receivers_enabled_renders_tcp_udp_blocks(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_hostname", lambda *_: "receiver-host")
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_ip", lambda *_: "10.0.0.10")

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": True,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.syslog "receiver" {' in rendered
    assert 'protocol = "udp"' in rendered
    assert 'protocol = "tcp"' in rendered
    assert 'receiver_hostname = "receiver-host"' in rendered
    assert 'receiver_ip = "10.0.0.10"' in rendered


def test_syslog_receivers_drop_logs_without_loki_endpoints(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_hostname", lambda *_: "receiver-host")
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_ip", lambda *_: "10.0.0.10")

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": True,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.syslog "receiver" {' in rendered
    assert "  forward_to = []" in rendered
    assert 'forward_to = [loki.process.juju.receiver]' not in rendered


def test_metrics_remote_write_renders_remote_scrape_jobs(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr(
        "charm.MetricsEndpointConsumer.jobs",
        lambda _self: [
            {
                "job_name": "juju_model_dummychain",
                "metrics_path": "/metrics",
                "scheme": "http",
                "scrape_interval": "30s",
                "scrape_timeout": "10s",
                "static_configs": [
                    {
                        "targets": ["10.0.0.20:9100"],
                        "labels": {
                            "juju_model": "remote-model",
                            "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
                            "juju_application": "dummychain",
                            "juju_unit": "dummychain/0",
                            "juju_charm": "dummychain",
                        },
                    }
                ],
                "relabel_configs": [],
            }
        ],
    )
    monkeypatch.setattr(
        "charm.PrometheusRemoteWriteConsumer.endpoints",
        property(lambda _self: [{"url": "http://10.0.0.10:9009/api/v1/push"}]),
    )

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'prometheus.remote_write "metrics" {' in rendered
    assert 'prometheus.scrape "juju_model_dummychain" {' in rendered
    assert '__address__ = "10.0.0.20:9100"' in rendered
    assert 'juju_unit = "dummychain/0"' in rendered
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


def test_metrics_targets_wait_for_remote_write(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr(
        "charm.MetricsEndpointConsumer.jobs",
        lambda _self: [
            {
                "job_name": "juju_model_dummychain",
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["10.0.0.20:9100"], "labels": {}}],
            }
        ],
    )
    monkeypatch.setattr(
        "charm.PrometheusRemoteWriteConsumer.endpoints",
        property(lambda _self: []),
    )

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(ctx.on.config_changed(), testing.State(config=config))

    assert 'prometheus.remote_write "metrics" {' not in seen["config"]
    assert 'prometheus.scrape "default" {' in seen["config"]
    assert "forward_to = []" in seen["config"]
    assert 'prometheus.scrape "juju_model_dummychain" {' not in seen["config"]
    assert state_out.unit_status == testing.WaitingStatus(
        "Waiting for remote write before enabling related metrics scraping"
    )
