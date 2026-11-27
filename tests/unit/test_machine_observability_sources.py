import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charms.dwellir_observability.v0.machine_observability import (
    MachineObservabilityPayload,
    MetricsEndpoint,
)

from config_builder import ConfigBuilder
from machine_observability_sources import translate_machine_observability_payload

TOPOLOGY = {
    "model": "m",
    "model_uuid": "u",
    "application": "iris",
    "unit": "iris/0",
    "charm_name": "iris-operator",
}


def _payload(*endpoints):
    return MachineObservabilityPayload.model_validate(
        {
            "schema_version": 2,
            "charm_name": "iris-operator",
            "source_topology": TOPOLOGY,
            "metrics_endpoints": list(endpoints),
            "systemd_units": ["iris.service"],
        }
    )


def test_endpoint_labels_are_merged_under_topology():
    source = translate_machine_observability_payload(
        _payload({"targets": ["localhost:9092"], "labels": {"gateway_region": "dc1"}})
    )

    assert source.metrics_scrape_jobs[0].targets[0].labels == {
        "gateway_region": "dc1",
        "juju_model": "m",
        "juju_model_uuid": "u",
        "juju_application": "iris",
        "juju_unit": "iris/0",
        "juju_charm": "iris-operator",
    }
    assert source.log_source_group is not None
    assert "gateway_region" not in source.log_source_group.topology_labels


def test_endpoint_labels_stay_per_endpoint():
    source = translate_machine_observability_payload(
        _payload(
            {"targets": ["localhost:1"], "labels": {"a": "1"}},
            {"targets": ["localhost:2"], "labels": {"b": "2"}},
        )
    )

    first, second = (job.targets[0].labels for job in source.metrics_scrape_jobs)
    assert first["a"] == "1" and "b" not in first
    assert second["b"] == "2" and "a" not in second


def test_endpoint_without_labels_keeps_topology_only():
    source = translate_machine_observability_payload(_payload({"targets": ["localhost:1"]}))

    assert set(source.metrics_scrape_jobs[0].targets[0].labels) == {
        "juju_model",
        "juju_model_uuid",
        "juju_application",
        "juju_unit",
        "juju_charm",
    }


def test_topology_labels_win():
    payload = _payload()
    payload.metrics_endpoints.append(
        MetricsEndpoint.model_construct(
            targets=["localhost:1"], labels={"juju_unit": "evil/9", "x": "y"}
        )
    )

    labels = (
        translate_machine_observability_payload(payload).metrics_scrape_jobs[0].targets[0].labels
    )

    assert labels["juju_unit"] == "iris/0"
    assert labels["x"] == "y"


def test_endpoint_label_values_are_escaped_in_rendered_config():
    source = translate_machine_observability_payload(
        _payload({"targets": ["localhost:1"], "labels": {"gateway_region": 'a"b\\c'}})
    )
    builder = ConfigBuilder(
        loki_endpoints=[],
        remote_write_endpoints=["http://mimir:9009/api/v1/push"],
        metrics_scrape_jobs=source.metrics_scrape_jobs,
        systemd_units=[],
        journal_kernel=False,
        journal_match_expressions=[],
        topology_labels={},
    )

    assert 'gateway_region = "a\\"b\\\\c"' in builder.build()


def test_unit_ip_labels_metrics_targets_only():
    source = translate_machine_observability_payload(
        _payload({"targets": ["localhost:9092"]}), "10.0.0.7"
    )

    assert source.metrics_scrape_jobs[0].targets[0].labels["unit_ip"] == "10.0.0.7"
    assert source.log_source_group is not None
    assert "unit_ip" not in source.log_source_group.topology_labels


def test_workload_unit_ip_label_wins_over_the_collector():
    source = translate_machine_observability_payload(
        _payload({"targets": ["localhost:9092"], "labels": {"unit_ip": "10.9.9.9"}}),
        "10.0.0.7",
    )

    assert source.metrics_scrape_jobs[0].targets[0].labels["unit_ip"] == "10.9.9.9"


def test_no_unit_ip_label_without_an_address():
    source = translate_machine_observability_payload(_payload({"targets": ["localhost:9092"]}))

    assert "unit_ip" not in source.metrics_scrape_jobs[0].targets[0].labels
