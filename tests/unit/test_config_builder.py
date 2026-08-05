# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.

import json

from config_builder import ConfigBuilder, HostMetricsCopy, MetricsScrapeJob, ScrapeTarget
from outbound_endpoints import OutboundEndpoint

TOPOLOGY = {
    "juju_model": "test-model",
    "juju_model_uuid": "00000000-0000-4000-8000-000000000001",
    "juju_application": "alloy",
    "juju_unit": "alloy/0",
    "juju_charm": "alloy",
}


def _builder(**kwargs) -> ConfigBuilder:
    defaults = {
        "loki_endpoints": [],
        "remote_write_endpoints": [],
        "metrics_scrape_jobs": [],
        "systemd_units": [],
        "journal_kernel": False,
        "journal_match_expressions": [],
        "live_debugging": False,
        "enable_syslog_receivers": False,
        "syslog_drop_access_logs": False,
        "syslog_drop_expressions": [],
        "syslog_rate_limit": 0,
        "syslog_rate_burst": 0,
        "receiver_hostname": "",
        "receiver_ip": "",
        "topology_labels": TOPOLOGY,
    }
    defaults.update(kwargs)
    return ConfigBuilder(**defaults)


def test_local_metrics_drop_without_remote_write():
    rendered = _builder().build()

    assert 'discovery.relabel "host_metrics" {' in rendered
    assert 'prometheus.scrape "host_metrics" {' in rendered
    assert 'job_name        = "alloy-local"' in rendered
    assert "forward_to      = []" in rendered
    assert 'prometheus.remote_write "metrics" {' not in rendered


def test_local_metrics_forward_to_remote_write_when_endpoint_exists():
    rendered = _builder(remote_write_endpoints=["http://10.0.0.10:9009/api/v1/push"]).build()

    assert 'prometheus.remote_write "metrics" {' in rendered
    assert 'url = "http://10.0.0.10:9009/api/v1/push"' in rendered
    assert 'max_keepalive_time = "30m"' in rendered
    assert "forward_to      = [prometheus.remote_write.metrics.receiver]" in rendered


def test_remote_write_renders_basic_auth_and_tls_config():
    expected_url = "https://prometheus-prod-39-prod-eu-north-0.grafana.net/api/prom/push"
    expected_ca = 'ca_pem = "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----\\n"'
    rendered = _builder(
        remote_write_endpoints=[
            OutboundEndpoint(
                url=expected_url,
                username="1076854",
                password="glc_token",
                tls_ca_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            )
        ]
    ).build()

    assert f'url = "{expected_url}"' in rendered
    assert "basic_auth {" in rendered
    assert 'username = "1076854"' in rendered
    assert 'password = "glc_token"' in rendered
    assert "tls_config {" in rendered
    assert expected_ca in rendered


def test_remote_scrape_jobs_are_rendered_with_topology_labels():
    rendered = _builder(
        remote_write_endpoints=["http://10.0.0.10:9009/api/v1/push"],
        metrics_scrape_jobs=[
            MetricsScrapeJob(
                job_name="juju_test_model_dummychain_prometheus_scrape",
                metrics_path="/metrics",
                scheme="http",
                scrape_interval="30s",
                scrape_timeout="10s",
                targets=[
                    ScrapeTarget(
                        address="10.0.0.20:9100",
                        labels={
                            "juju_model": "remote-model",
                            "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
                            "juju_application": "dummychain",
                            "juju_unit": "dummychain/0",
                            "juju_charm": "dummychain",
                        },
                    )
                ],
            )
        ],
    ).build()

    assert 'prometheus.scrape "juju_test_model_dummychain_prometheus_scrape" {' in rendered
    assert '__address__ = "10.0.0.20:9100"' in rendered
    assert 'juju_application = "dummychain"' in rendered
    assert 'juju_unit = "dummychain/0"' in rendered
    assert 'job_name = "juju_test_model_dummychain_prometheus_scrape"' in rendered
    assert 'scrape_interval = "30s"' in rendered
    assert 'scrape_timeout = "10s"' in rendered


def test_remote_scrape_jobs_render_tls_config():
    cert_pem = "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----\\n"
    key_pem = "-----BEGIN PRIVATE KEY-----\\nabc\\n-----END PRIVATE KEY-----\\n"
    rendered = _builder(
        remote_write_endpoints=["http://10.0.0.10:9009/api/v1/push"],
        metrics_scrape_jobs=[
            MetricsScrapeJob(
                job_name="juju_test_model_lxd_prometheus_scrape",
                metrics_path="/1.0/metrics",
                scheme="https",
                tls_config={
                    "insecure_skip_verify": True,
                    "cert_pem": cert_pem,
                    "key_pem": key_pem,
                },
                targets=[ScrapeTarget(address="[2001:db8::1]:9100")],
            )
        ],
    ).build()

    assert 'prometheus.scrape "juju_test_model_lxd_prometheus_scrape" {' in rendered
    assert 'scheme = "https"' in rendered
    assert "  tls_config {" in rendered
    assert "    insecure_skip_verify = true" in rendered
    assert f"    cert_pem = {json.dumps(cert_pem)}" in rendered
    assert f"    key_pem = {json.dumps(key_pem)}" in rendered


def test_syslog_receivers_without_loki_drop_remote_logs():
    rendered = _builder(
        enable_syslog_receivers=True,
        receiver_hostname="receiver-host",
        receiver_ip="10.0.0.10",
    ).build()

    assert 'loki.source.syslog "receiver" {' in rendered
    assert "  forward_to = []" in rendered
    assert 'loki.process "remote_syslog" {' not in rendered


def test_syslog_receivers_with_loki_use_remote_processor():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        enable_syslog_receivers=True,
        receiver_hostname="receiver-host",
        receiver_ip="10.0.0.10",
    ).build()

    assert 'loki.process "remote_syslog" {' in rendered
    assert "  forward_to = [loki.write.main.receiver]" in rendered
    assert 'loki.source.syslog "receiver" {' in rendered
    assert "  forward_to = [loki.process.remote_syslog.receiver]" in rendered
    assert 'target_label  = "syslog_app_name"' in rendered
    assert 'target_label  = "syslog_facility"' in rendered
    assert 'target_label  = "syslog_proc_id"' in rendered
    assert 'target_label  = "connection_hostname"' in rendered


def test_loki_writer_renders_basic_auth_and_tls_config():
    expected_url = "https://logs-prod-025.grafana.net/loki/api/v1/push"
    expected_ca = 'ca_pem = "-----BEGIN CERTIFICATE-----\\nabc\\n-----END CERTIFICATE-----\\n"'
    rendered = _builder(
        loki_endpoints=[
            OutboundEndpoint(
                url=expected_url,
                username="639149",
                password="glc_token",
                tls_ca_pem="-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n",
            )
        ],
        systemd_units=["ssh.service"],
    ).build()

    assert f'url = "{expected_url}"' in rendered
    assert "basic_auth {" in rendered
    assert 'username = "639149"' in rendered
    assert 'password = "glc_token"' in rendered
    assert "tls_config {" in rendered
    assert expected_ca in rendered


def test_syslog_drop_access_logs_renders_drop_stage():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        enable_syslog_receivers=True,
        syslog_drop_access_logs=True,
    ).build()

    assert 'loki.process "remote_syslog" {' in rendered
    assert "  stage.drop {" in rendered
    assert "(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|CONNECT|TRACE)" in rendered


def test_syslog_custom_drop_expressions_render_all_entries():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        enable_syslog_receivers=True,
        syslog_drop_expressions=["foo", "bar"],
    ).build()

    assert '    expression = "foo"' in rendered
    assert '    expression = "bar"' in rendered


def test_syslog_rate_limit_renders_limit_stage():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        enable_syslog_receivers=True,
        syslog_rate_limit=25,
        syslog_rate_burst=100,
    ).build()

    assert "  stage.limit {" in rendered
    assert "    rate = 25" in rendered
    assert "    burst = 100" in rendered
    assert "    drop = true" in rendered


def test_default_config_keeps_service_journal_path_only():
    rendered = _builder(systemd_units=["ssh.service"]).build()

    assert 'loki.relabel "journal" {' in rendered
    assert 'loki.source.journal "journald" {' in rendered
    assert 'matches = "_SYSTEMD_UNIT=ssh.service"' in rendered
    assert "relabel_rules = loki.relabel.journal.rules" in rendered
    assert 'labels = {log_source = "journal", systemd_unit = "ssh.service"}' in rendered
    assert "forward_to = [loki.process.juju.receiver]" in rendered
    assert 'loki.source.journal "host_journald" {' not in rendered


def test_journal_relabel_preserves_unit_identifier_and_priority_labels():
    rendered = _builder(systemd_units=["ssh.service"]).build()

    assert 'source_labels = ["__journal__systemd_unit"]' in rendered
    assert 'target_label  = "systemd_unit"' in rendered
    assert 'source_labels = ["__journal_syslog_identifier"]' in rendered
    assert 'target_label  = "syslog_identifier"' in rendered
    assert 'source_labels = ["__journal_priority_keyword"]' in rendered
    assert 'target_label  = "level"' in rendered
    assert 'source_labels = ["__journal_priority"]' in rendered
    assert 'target_label  = "severity"' in rendered


def test_journal_kernel_renders_unlabeled_host_journal_source():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        journal_kernel=True,
    ).build()

    assert 'loki.source.journal "host_journald" {' in rendered
    assert 'matches = "_TRANSPORT=kernel"' in rendered
    assert "relabel_rules = loki.relabel.journal.rules" in rendered
    assert 'labels = {log_source = "journal"}' in rendered
    assert "forward_to = [loki.write.main.receiver]" in rendered


def test_journal_match_expressions_render_once_and_ignore_blank_lines():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        journal_match_expressions=["SYSLOG_IDENTIFIER=lxd", "_TRANSPORT=kernel"],
    ).build()

    assert rendered.count("SYSLOG_IDENTIFIER=lxd") == 1
    assert rendered.count("_TRANSPORT=kernel") == 1
    assert rendered.count('loki.source.journal "host_journald_') == 2


def test_mixed_service_and_host_journal_sources_render_separately():
    rendered = _builder(
        loki_endpoints=["http://10.0.0.20:3100/loki/api/v1/push"],
        systemd_units=["ssh.service"],
        journal_kernel=True,
        journal_match_expressions=["SYSLOG_IDENTIFIER=lxd"],
    ).build()

    assert 'loki.source.journal "journald" {' in rendered
    assert 'matches = "_SYSTEMD_UNIT=ssh.service"' in rendered
    assert 'loki.source.journal "host_journald_0" {' in rendered
    assert 'loki.source.journal "host_journald_1" {' in rendered
    assert 'matches = "_TRANSPORT=kernel"' in rendered
    assert 'matches = "SYSLOG_IDENTIFIER=lxd"' in rendered


def test_host_journal_source_drops_without_loki_relation():
    rendered = _builder(
        journal_kernel=True,
        journal_match_expressions=["SYSLOG_IDENTIFIER=lxd"],
    ).build()

    assert 'loki.source.journal "host_journald_0" {' in rendered
    assert 'loki.source.journal "host_journald_1" {' in rendered
    assert "  forward_to = []" in rendered
    host_section = rendered.split('loki.source.journal "host_journald_0" {', 1)[1].split(
        'loki.process "juju" {', 1
    )[0]
    assert "juju_model" not in host_section


def test_host_metrics_and_alloy_self_render_as_separate_components():
    rendered = _builder(remote_write_endpoints=["http://mimir:9009/api/v1/push"]).build()

    assert 'discovery.relabel "host_metrics" {' in rendered
    assert "  targets = prometheus.exporter.unix.default.targets" in rendered
    assert 'discovery.relabel "alloy_self" {' in rendered
    assert '    __address__ = "127.0.0.1:6987",' in rendered
    assert 'prometheus.scrape "host_metrics" {' in rendered
    assert 'prometheus.scrape "alloy_self" {' in rendered
    assert 'discovery.relabel "local_metrics" {' not in rendered
    assert 'prometheus.scrape "default" {' not in rendered

    alloy_self = rendered.split('discovery.relabel "alloy_self" {', 1)[1].split("\n}", 1)[0]
    assert '    job         = "alloy",' in alloy_self


def test_the_exporter_targets_do_not_reach_the_alloy_self_component():
    rendered = _builder().build()

    alloy_self = rendered.split('discovery.relabel "alloy_self" {', 1)[1].split("\n}", 1)[0]

    assert "prometheus.exporter.unix.default.targets" not in alloy_self


def test_both_local_scrapes_keep_the_alloy_local_job_and_a_pinned_interval():
    rendered = _builder().build()

    for component in ('prometheus.scrape "host_metrics" {', 'prometheus.scrape "alloy_self" {'):
        block = rendered.split(component, 1)[1].split("\n}", 1)[0]
        assert '  job_name        = "alloy-local"' in block
        assert '  scrape_interval = "15s"' in block


def test_both_local_components_carry_the_charm_topology_labels():
    rendered = _builder().build()

    for component in ('discovery.relabel "host_metrics" {', 'discovery.relabel "alloy_self" {'):
        block = rendered.split(component, 1)[1].split("\n}", 1)[0]
        assert '    target_label = "juju_unit"\n    replacement  = "alloy/0"' in block


OP_NODE_COPY = HostMetricsCopy(
    component_name="op-node_0",
    topology_labels={
        "juju_model": "base-mainnet",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
        "juju_application": "op-node",
        "juju_unit": "op-node/0",
        "juju_charm": "op-node",
    },
)
OP_RETH_COPY = HostMetricsCopy(
    component_name="op-reth_2",
    topology_labels={
        "juju_model": "base-mainnet",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000002",
        "juju_application": "op-reth",
        "juju_unit": "op-reth/2",
        "juju_charm": "op-reth",
    },
)


def test_each_copy_renders_a_relabel_component_with_its_own_topology():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_RETH_COPY, OP_NODE_COPY],
    ).build()

    op_node = rendered.split('prometheus.relabel "op_node_0" {', 1)[1].split("\n}", 1)[0]

    assert "  forward_to = [prometheus.remote_write.metrics.receiver]" in op_node
    assert '    target_label = "juju_unit"\n    replacement  = "op-node/0"' in op_node
    assert '    target_label = "juju_application"\n    replacement  = "op-node"' in op_node
    assert '    target_label = "juju_charm"\n    replacement  = "op-node"' in op_node
    assert 'prometheus.relabel "op_reth_2" {' in rendered


def test_the_host_scrape_forwards_to_remote_write_and_every_copy():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_RETH_COPY, OP_NODE_COPY],
    ).build()

    assert (
        "  forward_to      = [prometheus.remote_write.metrics.receiver, "
        "prometheus.relabel.op_node_0.receiver, prometheus.relabel.op_reth_2.receiver]"
    ) in rendered


def test_the_alloy_self_scrape_never_forwards_to_a_copy():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_NODE_COPY],
    ).build()

    alloy_self = rendered.split('prometheus.scrape "alloy_self" {', 1)[1].split("\n}", 1)[0]

    assert "  forward_to      = [prometheus.remote_write.metrics.receiver]" in alloy_self


def test_copies_are_dropped_without_a_remote_write_upstream():
    rendered = _builder(host_metrics_copies=[OP_NODE_COPY]).build()

    assert "prometheus.relabel" not in rendered
    assert "  forward_to      = []" in rendered


def test_no_copies_renders_only_the_collectors_own_pipeline():
    rendered = _builder(remote_write_endpoints=["http://mimir:9009/api/v1/push"]).build()

    assert "prometheus.relabel" not in rendered
    assert "  forward_to      = [prometheus.remote_write.metrics.receiver]" in rendered


def test_partial_copy_clears_the_labels_it_does_not_set():
    partial_copy = HostMetricsCopy(
        component_name="partial",
        topology_labels={
            "juju_application": "op-node",
            "juju_unit": "op-node/0",
        },
    )
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[partial_copy],
    ).build()

    block = rendered.split('prometheus.relabel "partial" {', 1)[1].split("\n}", 1)[0]

    assert '    target_label = "juju_application"\n    replacement  = "op-node"' in block
    assert '    target_label = "juju_unit"\n    replacement  = "op-node/0"' in block
    assert '    action = "labeldrop"\n    regex  = "juju_model"' in block
    assert '    action = "labeldrop"\n    regex  = "juju_model_uuid"' in block
    assert '    action = "labeldrop"\n    regex  = "juju_charm"' in block


def test_empty_copy_renders_no_component_and_no_forward_to_entry():
    empty_copy = HostMetricsCopy(component_name="empty", topology_labels={})
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[empty_copy],
    ).build()

    assert 'prometheus.relabel "empty" {' not in rendered
    assert "prometheus.relabel.empty.receiver" not in rendered


def test_fully_populated_copy_still_renders_only_replace_rules():
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[OP_NODE_COPY],
    ).build()

    block = rendered.split('prometheus.relabel "op_node_0" {', 1)[1].split("\n}", 1)[0]

    assert block.count("  rule {") == 5
    assert block.count("replacement  =") == 5
    assert "labeldrop" not in block


def test_copies_that_sanitize_to_the_same_name_render_once():
    duplicate_a = HostMetricsCopy(
        component_name="op node",
        topology_labels={"juju_application": "op-node", "juju_unit": "op-node/0"},
    )
    duplicate_b = HostMetricsCopy(
        component_name="op_node",
        topology_labels={"juju_application": "op-node-dup", "juju_unit": "op-node-dup/0"},
    )
    rendered = _builder(
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        host_metrics_copies=[duplicate_a, duplicate_b],
    ).build()

    assert rendered.count('prometheus.relabel "op_node" {') == 1
