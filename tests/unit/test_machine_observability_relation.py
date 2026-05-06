import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import PropertyMock, patch

from ops import testing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

from charm import AlloyCharm

DEFAULT_ARGS = "--server.http.listen-addr=0.0.0.0:6987"


def _patch_runtime():
    return (
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.verify_config"),
        patch("charm.alloy.restart"),
        patch("charm.alloy.reload"),
        patch("charm.AlloyCharm._syslog_receiver_hostname", return_value="alloy-host"),
        patch("charm.AlloyCharm._syslog_receiver_ip", return_value="10.0.0.10"),
        patch("charm.alloy.read_custom_args", return_value=DEFAULT_ARGS),
        patch("charm.alloy.write_custom_args"),
        patch("charm.AlloyCharm._write_alloy_systemd_unit_defaults"),
    )


def test_machine_observability_v2_relation_renders_metrics_and_logs():
    seen: dict[str, str] = {}
    with ExitStack() as stack:
        for manager in _patch_runtime():
            stack.enter_context(manager)
        stack.enter_context(
            patch(
                "charm.PrometheusRemoteWriteConsumer.endpoints",
                new_callable=PropertyMock,
                return_value=[{"url": "http://mimir:9009/api/v1/push"}],
            )
        )
        stack.enter_context(
            patch(
                "charm.LokiPushApiConsumer.loki_endpoints",
                new_callable=PropertyMock,
                return_value=[{"url": "http://loki:3100/loki/api/v1/push"}],
            )
        )
        stack.enter_context(
            patch(
                "charm.alloy.write_config_text",
                side_effect=lambda config_text, **_: seen.__setitem__("config", config_text),
            )
        )
        harness = testing.Harness(AlloyCharm)
        harness.begin()

        relation_id = harness.add_relation("machine-observability", "op-node")
        harness.add_relation_unit(relation_id, "op-node/0")
        harness.update_relation_data(
            relation_id,
            "op-node",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 2,
                        "charm_name": "op-node",
                        "source_topology": {
                            "model": "base-mainnet-ovh-us-west-2",
                            "model_uuid": "00000000-0000-4000-8000-000000000042",
                            "application": "op-node",
                            "unit": "op-node/0",
                            "charm_name": "op-node",
                        },
                        "metrics_endpoints": [
                            {
                                "targets": ["localhost:7300"],
                                "path": "/metrics",
                                "scheme": "http",
                                "interval": "",
                                "timeout": "",
                                "tls": {},
                            }
                        ],
                        "systemd_units": ["opnode.service"],
                        "journal_match_expressions": [],
                        "log_files": [
                            {
                                "include": ["/var/log/op-node/*.log"],
                                "exclude": ["/var/log/op-node/debug.log"],
                                "attributes": {"service_name": "op-node"},
                            }
                        ],
                    }
                )
            },
        )

    rendered = seen["config"]
    assert 'job_name = "op-node_0"' in rendered
    assert '__address__ = "localhost:7300"' in rendered
    assert 'juju_application = "op-node"' in rendered
    assert 'juju_unit = "op-node/0"' in rendered
    assert 'juju_charm = "op-node"' in rendered
    assert 'matches = "_SYSTEMD_UNIT=opnode.service"' in rendered
    assert "/var/log/op-node/*.log" in rendered
    assert "/var/log/op-node/debug.log" in rendered


def test_machine_observability_v1_relation_is_blocked():
    with ExitStack() as stack:
        for manager in _patch_runtime():
            stack.enter_context(manager)
        stack.enter_context(patch("charm.alloy.write_config_text"))
        harness = testing.Harness(AlloyCharm)
        harness.begin()

        relation_id = harness.add_relation("machine-observability", "polkadot")
        harness.add_relation_unit(relation_id, "polkadot/0")
        harness.update_relation_data(
            relation_id,
            "polkadot",
            {
                "payload": json.dumps(
                    {
                        "schema_version": 1,
                        "charm_name": "polkadot",
                        "metrics_endpoints": [],
                        "systemd_units": ["snap.polkadot.polkadot.service"],
                        "journal_match_expressions": [],
                        "log_files": [],
                    }
                )
            },
        )

    assert harness.charm.unit.status.name == "blocked"
    assert "schema_version 2" in harness.charm.unit.status.message
