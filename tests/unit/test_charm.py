# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import json
import socket
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


def test_config_changed_reloads_active_service_when_runtime_args_unchanged(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    reloaded: list[str] = []
    restarted: list[str] = []
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.is_active", lambda: True)
    monkeypatch.setattr("charm.alloy.reload", lambda: reloaded.append("reload"))
    monkeypatch.setattr("charm.alloy.restart", lambda: restarted.append("restart"))
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.write_config_text", lambda *_, **__: None)

    first = {
        "config-override": "logging { level = \"warn\" }\n",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "log-level": "info",
    }
    second = {**first, "config-override": "logging { level = \"info\" }\n"}

    state = testing.State(config=first)
    state = ctx.run(ctx.on.config_changed(), state)
    state = ctx.run(ctx.on.config_changed(), replace(state, config=second))

    assert reloaded == ["reload"]
    assert restarted == ["restart"]


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
        "syslog-drop-access-logs": False,
        "syslog-drop-expressions": "",
        "syslog-rate-limit": 0,
        "syslog-rate-burst": 0,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.syslog "receiver" {' in rendered
    assert 'loki.process "remote_syslog" {' not in rendered
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
        "syslog-drop-access-logs": False,
        "syslog-drop-expressions": "",
        "syslog-rate-limit": 0,
        "syslog-rate-burst": 0,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.syslog "receiver" {' in rendered
    assert "  forward_to = []" in rendered
    assert 'loki.process "remote_syslog" {' not in rendered
    assert 'forward_to = [loki.process.juju.receiver]' not in rendered


def test_syslog_receivers_with_loki_render_remote_processor(monkeypatch):
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
        "charm.AlloyCharm._loki_endpoint_urls",
        lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"],
    )
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
        "syslog-drop-access-logs": False,
        "syslog-drop-expressions": "",
        "syslog-rate-limit": 0,
        "syslog-rate-burst": 0,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.process "remote_syslog" {' in rendered
    assert "  forward_to = [loki.write.main.receiver]" in rendered
    assert "  forward_to = [loki.process.remote_syslog.receiver]" in rendered


def test_syslog_drop_controls_render_access_and_limit_stages(monkeypatch):
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
        "charm.AlloyCharm._loki_endpoint_urls",
        lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"],
    )
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
        "syslog-drop-access-logs": True,
        "syslog-drop-expressions": "foo\nbar",
        "syslog-rate-limit": 25,
        "syslog-rate-burst": 100,
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.process "remote_syslog" {' in rendered
    assert '    expression = "foo"' in rendered
    assert '    expression = "bar"' in rendered
    assert "(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE)" in rendered
    assert "    rate = 25" in rendered
    assert "    burst = 100" in rendered


def test_journal_kernel_renders_host_journal_source(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr(
        "charm.AlloyCharm._loki_endpoint_urls",
        lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"],
    )

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "journal-kernel": True,
        "journal-match-expressions": "",
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.journal "host_journald" {' in rendered
    assert 'matches = "_TRANSPORT=kernel"' in rendered
    assert 'forward_to = [loki.write.main.receiver]' in rendered


def test_journal_match_expressions_render_without_juju_topology(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr(
        "charm.AlloyCharm._loki_endpoint_urls",
        lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"],
    )

    def write_config_text(config_text: str, **_):
        seen["config"] = config_text

    monkeypatch.setattr("charm.alloy.write_config_text", write_config_text)

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "journal-kernel": False,
        "journal-match-expressions": "SYSLOG_IDENTIFIER=lxd\n\n_TRANSPORT=kernel",
        "systemd-units": "",
        "log-level": "info",
    }

    ctx.run(ctx.on.config_changed(), testing.State(config=config))

    rendered = seen["config"]
    assert 'loki.source.journal "host_journald_0" {' in rendered
    assert 'matches = "SYSLOG_IDENTIFIER=lxd"' in rendered
    assert 'matches = "_TRANSPORT=kernel"' in rendered
    host_section = rendered.split('loki.source.journal "host_journald_0" {', 1)[1].split(
        'loki.process "juju" {', 1
    )[0]
    assert "juju_model" not in host_section


def test_syslog_receiver_relation_publishes_ready_receiver(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    relation = testing.Relation(
        endpoint="syslog-receiver",
        interface="syslog",
        remote_app_name="haproxy-dataplane-api",
    )
    monkeypatch.setattr("charm.AlloyCharm._loki_endpoint_urls", lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"])
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_ip", lambda *_: "10.0.0.10")

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": True,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(
        ctx.on.relation_created(relation),
        testing.State(leader=True, config=config, relations=[relation]),
    )

    relation_out = state_out.get_relation(relation.id)
    assert relation_out.local_unit_data == {
        "address": "10.0.0.10",
        "port": "1514",
        "protocols": "tcp,udp",
        "recommended-protocol": "tcp",
        "ready": "true",
        "reason": "ready",
    }
    assert relation_out.local_app_data == relation_out.local_unit_data


def test_syslog_receiver_relation_not_ready_without_loki(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    relation = testing.Relation(
        endpoint="syslog-receiver",
        interface="syslog",
        remote_app_name="haproxy-dataplane-api",
    )
    monkeypatch.setattr("charm.AlloyCharm._loki_endpoint_urls", lambda *_: [])
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_ip", lambda *_: "10.0.0.10")

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": True,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(
        ctx.on.relation_created(relation),
        testing.State(leader=True, config=config, relations=[relation]),
    )

    relation_out = state_out.get_relation(relation.id)
    assert relation_out.local_unit_data == {
        "address": "10.0.0.10",
        "port": "1514",
        "protocols": "tcp,udp",
        "recommended-protocol": "tcp",
        "ready": "false",
        "reason": "waiting for send-loki-logs relation",
    }


def test_syslog_receiver_relation_clears_connection_details_when_disabled(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    relation = testing.Relation(
        endpoint="syslog-receiver",
        interface="syslog",
        remote_app_name="haproxy-dataplane-api",
        local_app_data={"address": "stale", "port": "1514"},
        local_unit_data={"address": "stale", "port": "1514"},
    )
    monkeypatch.setattr("charm.AlloyCharm._loki_endpoint_urls", lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"])

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": False,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(
        ctx.on.relation_created(relation),
        testing.State(leader=True, config=config, relations=[relation]),
    )

    relation_out = state_out.get_relation(relation.id)
    assert relation_out.local_unit_data == {
        "ready": "false",
        "reason": "syslog receivers disabled",
        "recommended-protocol": "tcp",
    }
    assert relation_out.local_app_data == relation_out.local_unit_data


def test_config_changed_refreshes_syslog_receiver_relation(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    relation = testing.Relation(
        endpoint="syslog-receiver",
        interface="syslog",
        remote_app_name="haproxy-dataplane-api",
    )
    monkeypatch.setattr("charm.alloy.ensure_config_dir_permissions", lambda *_: None)
    monkeypatch.setattr("charm.alloy.verify_config", lambda **_: None)
    monkeypatch.setattr("charm.alloy.restart", lambda: None)
    monkeypatch.setattr("charm.alloy.reload", lambda: None)
    monkeypatch.setattr("charm.AlloyCharm._write_alloy_systemd_unit_defaults", lambda *_: None)
    monkeypatch.setattr("charm.alloy.read_custom_args", lambda: DEFAULT_ARGS)
    monkeypatch.setattr("charm.alloy.write_custom_args", lambda *_: None)
    monkeypatch.setattr("charm.alloy.write_config_text", lambda *_, **__: None)
    monkeypatch.setattr("charm.AlloyCharm._loki_endpoint_urls", lambda *_: ["http://10.0.0.20:3100/loki/api/v1/push"])
    monkeypatch.setattr("charm.AlloyCharm._syslog_receiver_ip", lambda *_: "10.0.0.10")

    config = {
        "config-override": "",
        "custom_args": DEFAULT_ARGS,
        "alloy-livedebugging": False,
        "enable-syslogreceivers": True,
        "systemd-units": "",
        "log-level": "info",
    }

    state_out = ctx.run(
        ctx.on.config_changed(),
        testing.State(leader=True, config=config, relations=[relation]),
    )

    relation_out = state_out.get_relation(relation.id)
    assert relation_out.local_unit_data["ready"] == "true"
    assert relation_out.local_unit_data["address"] == "10.0.0.10"
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


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


def test_metrics_remote_write_honors_provider_job_name_override(monkeypatch):
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
                "job_name": "juju_lxd_hosts_generated_lxd-0",
                "metrics_path": "/1.0/metrics",
                "scheme": "https",
                "static_configs": [
                    {
                        "targets": ["10.0.0.20:8444"],
                        "labels": {
                            "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
                            "juju_application": "lxd-host",
                            "juju_unit": "lxd-host/0",
                        },
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "charm.AlloyCharm._metrics_job_name_overrides",
        lambda _self: {
            (
                "00000000-0000-4000-8000-000000000002",
                "lxd-host",
                "lxd-host/0",
            ): "lxd-node1"
        },
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
    assert 'prometheus.scrape "lxd_node1" {' in rendered
    assert 'job_name = "lxd-node1"' in rendered
    assert 'job_name = "juju_lxd_hosts_generated_lxd-0"' not in rendered
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


def test_metrics_remote_write_keeps_generated_job_name_without_override(monkeypatch):
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
                "job_name": "juju_other_model_generated_service-0",
                "metrics_path": "/metrics",
                "scheme": "http",
                "static_configs": [
                    {
                        "targets": ["10.0.0.21:9100"],
                        "labels": {
                            "juju_model_uuid": "00000000-0000-4000-8000-000000000003",
                            "juju_application": "other-service",
                            "juju_unit": "other-service/0",
                        },
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr("charm.AlloyCharm._metrics_job_name_overrides", lambda _self: {})
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
    assert 'prometheus.scrape "juju_other_model_generated_service_0" {' in rendered
    assert 'job_name = "juju_other_model_generated_service-0"' in rendered
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


def test_metrics_remote_write_renders_tls_scrape_jobs(monkeypatch):
    ctx = testing.Context(AlloyCharm)
    seen = {}
    cert_pem = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
    key_pem = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
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
                "job_name": "juju_model_lxd",
                "metrics_path": "/1.0/metrics",
                "scheme": "https",
                "static_configs": [
                    {
                        "targets": ["[2001:db8::1]:9100"],
                        "labels": {"juju_unit": "lxd/0"},
                    }
                ],
                "tls_config": {
                    "insecure_skip_verify": True,
                    "cert_file": cert_pem,
                    "key_file": key_pem,
                },
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
    assert 'prometheus.scrape "juju_model_lxd" {' in rendered
    assert 'scheme = "https"' in rendered
    assert "  tls_config {" in rendered
    assert '    insecure_skip_verify = true' in rendered
    assert f"    cert_pem = {json.dumps(cert_pem)}" in rendered
    assert f"    key_pem = {json.dumps(key_pem)}" in rendered
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


def test_metrics_remote_write_prefers_reverse_dns_hostname_for_ipv6_scrape_targets(monkeypatch):
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
                "job_name": "juju_model_dummychain_ipv6",
                "metrics_path": "/metrics",
                "static_configs": [
                    {
                        "targets": ["2001:db8:1234:1:216:3eff:fe67:c007:9090"],
                        "labels": {"juju_unit": "dummychain/0"},
                    }
                ],
            }
        ],
    )
    monkeypatch.setattr(
        "charm.PrometheusRemoteWriteConsumer.endpoints",
        property(lambda _self: [{"url": "http://[2001:db8::1]:9009/api/v1/push"}]),
    )
    monkeypatch.setattr(
        "charm.socket.gethostbyaddr",
        lambda host: ("juju-595a8a-6.lxd", [], [host]),
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
    assert '__address__ = "juju-595a8a-6.lxd:9090"' in rendered
    assert state_out.unit_status == testing.ActiveStatus("Alloy config updated and valid")


def test_metrics_remote_write_brackets_ipv6_scrape_targets_when_reverse_dns_missing(monkeypatch):
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
                "job_name": "juju_model_dummychain_ipv6",
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["2001:db8:1234:1:216:3eff:fe67:c007:9090"]}],
            }
        ],
    )
    monkeypatch.setattr(
        "charm.PrometheusRemoteWriteConsumer.endpoints",
        property(lambda _self: [{"url": "http://[2001:db8::1]:9009/api/v1/push"}]),
    )
    monkeypatch.setattr(
        "charm.socket.gethostbyaddr",
        lambda host: (_ for _ in ()).throw(socket.herror()),
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
    assert '__address__ = "[2001:db8:1234:1:216:3eff:fe67:c007]:9090"' in rendered
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
