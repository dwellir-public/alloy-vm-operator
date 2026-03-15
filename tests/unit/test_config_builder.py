# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.

from config_builder import ConfigBuilder, MetricsScrapeJob, ScrapeTarget

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
        "live_debugging": False,
        "enable_syslog_receivers": False,
        "receiver_hostname": "",
        "receiver_ip": "",
        "topology_labels": TOPOLOGY,
    }
    defaults.update(kwargs)
    return ConfigBuilder(**defaults)


def test_local_metrics_drop_without_remote_write():
    rendered = _builder().build()

    assert 'discovery.relabel "local_metrics" {' in rendered
    assert 'prometheus.scrape "default" {' in rendered
    assert 'job_name   = "alloy-local"' in rendered
    assert "forward_to = []" in rendered
    assert 'prometheus.remote_write "metrics" {' not in rendered


def test_local_metrics_forward_to_remote_write_when_endpoint_exists():
    rendered = _builder(
        remote_write_endpoints=["http://10.0.0.10:9009/api/v1/push"]
    ).build()

    assert 'prometheus.remote_write "metrics" {' in rendered
    assert 'url = "http://10.0.0.10:9009/api/v1/push"' in rendered
    assert 'max_keepalive_time = "30m"' in rendered
    assert "forward_to = [prometheus.remote_write.metrics.receiver]" in rendered


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
