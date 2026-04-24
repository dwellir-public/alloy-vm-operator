import re
from unittest.mock import patch

from ops import testing

from charm import AlloyCharm

DEFAULT_ARGS = "--server.http.listen-addr=0.0.0.0:6987"
MODEL_NAME = "tenant-routing"
MODEL_UUID = "00000000-0000-4000-8000-000000000111"


def _expected_tenant_id(application: str, model_uuid: str) -> str:
    short_model_uuid = re.sub(r"[^a-z0-9]+", "", model_uuid.lower())[:8]
    base = f"{application}-{short_model_uuid}" if short_model_uuid else application
    return re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")


def test_send_remote_write_relation_publishes_tenant_metadata():
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

    assert relation_data["application"] == harness.charm.app.name
    assert relation_data["model"] == MODEL_NAME
    assert relation_data["model_uuid"] == MODEL_UUID
    assert relation_data["tenant-id"] == _expected_tenant_id(harness.charm.app.name, MODEL_UUID)
