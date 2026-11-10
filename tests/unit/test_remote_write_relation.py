import json
from unittest.mock import patch

import ops
from ops import testing

from charm import AlloyCharm
from host_alert_rules import build_host_alert_rule_groups

DEFAULT_ARGS = "--server.http.listen-addr=0.0.0.0:6987"
MODEL_NAME = "tenant-routing"
MODEL_UUID = "00000000-0000-4000-8000-000000000111"


def test_send_remote_write_relation_publishes_host_rules_and_standard_metadata():
    with (
        patch("charm.alloy.ensure_config_dir_permissions"),
        patch("charm.alloy.verify_config"),
        patch("charm.alloy.restart"),
        patch("charm.alloy.reload"),
        patch("charm.AlloyCharm._syslog_receiver_hostname", return_value="alloy-host"),
        patch("charm.AlloyCharm._syslog_receiver_ip", return_value="10.0.0.10"),
        patch("charm.alloy.read_custom_args", return_value=DEFAULT_ARGS),
        patch("charm.alloy.write_custom_args"),
        patch("charm.alloy.write_config_text"),
        patch("charm.AlloyCharm._write_alloy_systemd_unit_defaults"),
    ):
        harness = testing.Harness(AlloyCharm)
        harness.set_leader(True)
        harness.set_model_name(MODEL_NAME)
        harness.set_model_uuid(MODEL_UUID)
        harness.begin()

        relation_id = harness.add_relation("send-remote-write", "mimir-gateway-vm")
        harness.add_relation_unit(relation_id, "mimir-gateway-vm/0")

        relation_data = harness.get_relation_data(relation_id, harness.charm.app.name)

    assert json.loads(relation_data["alert_rules"]) == {
        "groups": build_host_alert_rule_groups(
            model_uuid=MODEL_UUID, application=harness.charm.app.name
        )
    }
    assert json.loads(relation_data["metadata"]) == {
        "application": harness.charm.app.name,
        "model": MODEL_NAME,
        "model_uuid": MODEL_UUID,
        "unit": f"{harness.charm.app.name}/0",
    }
    assert "tenant-id" not in relation_data


def _leader_harness(monkeypatch, *, leader=True, with_remote_write=True):
    monkeypatch.setattr(
        AlloyCharm, "_configure", lambda *_args, **_kwargs: ops.ActiveStatus("configured")
    )
    harness = testing.Harness(AlloyCharm)
    harness.set_leader(leader)
    harness.set_model_name(MODEL_NAME)
    harness.set_model_uuid(MODEL_UUID)
    harness.begin()
    relation_id = None
    if with_remote_write:
        relation_id = harness.add_relation("send-remote-write", "mimir-gateway-vm")
    return harness, relation_id


def _published_groups(harness, relation_id):
    raw = harness.get_relation_data(relation_id, harness.charm.app.name)["alert_rules"]
    return json.loads(raw)["groups"]


def test_host_alert_group_is_published_by_default(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch)

    harness.update_config({"log-level": "debug"})

    assert _published_groups(harness, relation_id) == build_host_alert_rule_groups(
        model_uuid=MODEL_UUID, application=harness.charm.app.name
    )


def test_disabling_host_alarms_withdraws_the_host_alert_group(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch)
    harness.update_config({"log-level": "debug"})
    assert _published_groups(harness, relation_id)

    harness.update_config({"enable-host-alarms": False})

    assert _published_groups(harness, relation_id) == []


def test_non_leader_does_not_publish_host_alert_rules(monkeypatch):
    harness, relation_id = _leader_harness(monkeypatch, leader=False)

    harness.update_config({"log-level": "debug"})

    assert relation_id is not None
    assert "alert_rules" not in harness.get_relation_data(relation_id, harness.charm.app.name)


def test_host_alert_rules_do_not_change_destination_status(monkeypatch):
    """Host metrics may go to Grafana Cloud only, so alarms never demand remote write."""
    harness, _ = _leader_harness(monkeypatch, with_remote_write=False)

    harness.update_config({"log-level": "debug"})

    assert harness.charm.unit.status == ops.ActiveStatus("configured")
