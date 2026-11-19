# Copyright 2025 Erik Lönroth
# See LICENSE file for licensing details.

"""The charm refuses to touch an Alloy installation this unit does not own."""

import dataclasses
import json
import subprocess
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from charms.dwellir_observability.v0.machine_observability import encode_artifact
from ops import testing

import alloy_ownership
from charm import AlloyCharm

pytestmark = pytest.mark.real_ownership

UNIT = "alloy-vm/0"
DEFAULT_ARGS = "--server.http.listen-addr=0.0.0.0:6987"
# Every call that changes the machine's Alloy, in the order they happen.
MUTATING = (
    "install",
    "start",
    "stop",
    "restart",
    "reload",
    "write_config_text",
    "write_custom_args",
    "preserve_default_config",
    "ensure_config_dir_permissions",
)
SYSTEMD_DEFAULTS = "_write_alloy_systemd_unit_defaults"
_RETURNS = {"get_version": "1.0.0", "is_active": True, "read_custom_args": DEFAULT_ARGS}
OTHER = "Alloy on this machine is managed by other-sub/3; remove this unit or other-sub/3"
UNMANAGED = "unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy"
# Stored keys this revision adds; a unit from an older revision has none of them.
_NEW_STORED_KEYS = ("install_hook_seen", "install_pending", "alloy_installed")
APT_DOWN = subprocess.CalledProcessError(100, ["apt-get", "install", "-y", "alloy"])


@pytest.fixture(autouse=True)
def config_path(tmp_path, monkeypatch):
    """Point the charm's Alloy config at a temporary file."""
    path = tmp_path / "etc-alloy" / "config.alloy"
    monkeypatch.setattr("charm.DEFAULT_CONFIG_PATH", str(path))
    monkeypatch.setattr("charm.DEFAULT_CONFIG_BACKUP_PATH", str(path) + ".backup")
    return path


def _write_config(config_text, *, config_path, **_kwargs):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config_text, encoding="utf-8")


def _relations():
    return [
        testing.Relation("send-loki-logs", remote_app_name="loki"),
        testing.Relation("send-remote-write", remote_app_name="mimir"),
        testing.Relation("machine-observability", remote_app_name="polkadot"),
    ]


def _relation_event(name):
    def build(ctx, state):
        relation = next(relation for relation in state.relations if relation.endpoint == name)
        return ctx.on.relation_changed(relation)

    return build


EVENTS = {
    "install": lambda ctx, _state: ctx.on.install(),
    "start": lambda ctx, _state: ctx.on.start(),
    "config_changed": lambda ctx, _state: ctx.on.config_changed(),
    "update_status": lambda ctx, _state: ctx.on.update_status(),
    "upgrade_charm": lambda ctx, _state: ctx.on.upgrade_charm(),
    "send-loki-logs": _relation_event("send-loki-logs"),
    "send-remote-write": _relation_event("send-remote-write"),
    "machine-observability": _relation_event("machine-observability"),
}
# Every hook that can reach Alloy; stop only ever touches the marker.
GATED_EVENTS = list(EVENTS)
EVENTS["stop"] = lambda ctx, _state: ctx.on.stop()


def _patch_installation(installation):
    """Patch the probe to report ``installation``, or to raise it when it is an exception."""
    if isinstance(installation, BaseException):
        return patch("charm.alloy.installation", side_effect=installation)
    return patch("charm.alloy.installation", return_value=installation)


def _mock_alloy(stack, installation, *, install_error=None, side_effects=None):
    """Mock every Alloy call; return mocks by name, with ``calls`` recording mutating calls."""
    stack.enter_context(_patch_installation(installation))
    mocks = {"calls": MagicMock()}
    for name in MUTATING:
        mocks[name] = stack.enter_context(patch(f"charm.alloy.{name}", new=MagicMock()))
        mocks["calls"].attach_mock(mocks[name], name)
    mocks[SYSTEMD_DEFAULTS] = stack.enter_context(
        patch.object(AlloyCharm, SYSTEMD_DEFAULTS, new=MagicMock())
    )
    mocks["calls"].attach_mock(mocks[SYSTEMD_DEFAULTS], SYSTEMD_DEFAULTS)
    mocks["verify_config"] = stack.enter_context(patch("charm.alloy.verify_config"))
    for name, value in _RETURNS.items():
        mocks[name] = stack.enter_context(patch(f"charm.alloy.{name}", return_value=value))
    stack.enter_context(
        patch.object(AlloyCharm, "_syslog_receiver_hostname", return_value="alloy-host")
    )
    stack.enter_context(patch.object(AlloyCharm, "_syslog_receiver_ip", return_value="10.0.0.10"))
    mocks["write_config_text"].side_effect = _write_config
    mocks["install"].side_effect = install_error
    for name, effect in (side_effects or {}).items():
        mocks[name].side_effect = effect
    return mocks


def _run(event, state, *, installation, install_error=None, side_effects=None):
    """Run one hook with every Alloy call mocked; return (state_out, mocks by name)."""
    ctx = testing.Context(AlloyCharm)
    resolved = EVENTS[event](ctx, state) if isinstance(event, str) else event(ctx, state)
    with ExitStack() as stack:
        mocks = _mock_alloy(
            stack, installation, install_error=install_error, side_effects=side_effects
        )
        state_out = ctx.run(resolved, state)
    return state_out, mocks


def _call_names(mocks):
    return [call[0] for call in mocks["calls"].mock_calls]


def _stored(**content):
    return testing.StoredState(owner_path="AlloyCharm", content=content)


def _charm_stored(state):
    return state.get_stored_state("_stored", owner_path="AlloyCharm").content


def _installed_here(relations=None):
    """Return the state of a unit that ran this revision's install hook."""
    return testing.State(
        relations=_relations() if relations is None else relations,
        stored_states=[_stored(install_hook_seen=True)],
    )


def _legacy(relations=None):
    """Return the state of a configured unit from a revision that predates the marker."""
    with patch.object(AlloyCharm, "_ensure_alloy_ownership", lambda _self, **_kwargs: True):
        configured, _ = _run(
            "config_changed",
            testing.State(relations=_relations() if relations is None else relations),
            installation="package",
        )
    content = {
        key: value
        for key, value in _charm_stored(configured).items()
        if key not in _NEW_STORED_KEYS
    }
    others = [
        stored
        for stored in configured.stored_states
        if (stored.owner_path, stored.name) != ("AlloyCharm", "_stored")
    ]
    return dataclasses.replace(configured, stored_states=[*others, _stored(**content)])


def _assert_untouched(mocks):
    for name in (*MUTATING, SYSTEMD_DEFAULTS):
        mocks[name].assert_not_called()


def _marker_bytes(marker_dir):
    path = marker_dir / alloy_ownership.MARKER_NAME
    return path.read_bytes() if path.exists() else None


def test_install_claims_and_installs_on_a_clean_machine(alloy_marker_dir):
    state_out, mocks = _run("install", testing.State(), installation="none")

    assert _call_names(mocks) == ["install", "preserve_default_config"]
    assert alloy_ownership.current_owner() == UNIT
    assert state_out.unit_status == testing.MaintenanceStatus("Installing Alloy")
    assert state_out.deferred == []


@pytest.mark.parametrize("event_name", GATED_EVENTS)
def test_unit_blocks_and_touches_nothing_when_another_unit_owns_alloy(
    alloy_marker_dir, event_name
):
    alloy_ownership.claim("other-sub/3", installed=False)
    before = _marker_bytes(alloy_marker_dir)
    state_out, mocks = _run(
        event_name, testing.State(relations=_relations()), installation="package"
    )

    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)
    assert _marker_bytes(alloy_marker_dir) == before


@pytest.mark.parametrize("event_name", GATED_EVENTS)
@pytest.mark.parametrize("installation", ["package", "other"])
def test_unit_blocks_on_unmanaged_alloy(alloy_marker_dir, event_name, installation):
    state_out, mocks = _run(event_name, _installed_here(), installation=installation)

    assert state_out.unit_status == testing.BlockedStatus(UNMANAGED)
    _assert_untouched(mocks)
    assert _marker_bytes(alloy_marker_dir) is None


@pytest.mark.parametrize("event_name", ["upgrade_charm", "config_changed", "update_status"])
def test_legacy_unit_claims_its_running_alloy_on_its_first_hook(alloy_marker_dir, event_name):
    state_out, mocks = _run(event_name, _legacy(), installation="package")

    assert alloy_ownership.current_owner() == UNIT
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)
    mocks["preserve_default_config"].assert_called_once()
    mocks["install"].assert_not_called()
    mocks["stop"].assert_not_called()


@pytest.mark.parametrize("event_name", ["upgrade_charm", "update_status"])
def test_legacy_unit_keeps_alloy_running_untouched_on_refresh(alloy_marker_dir, event_name):
    _, mocks = _run(event_name, _legacy(), installation="package")

    for name in ("install", "start", "stop", "restart", "reload", "write_config_text"):
        mocks[name].assert_not_called()
    mocks[SYSTEMD_DEFAULTS].assert_not_called()


def test_second_legacy_unit_on_the_machine_blocks(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=True, allow_existing=True)
    legacy = _legacy()
    state_out, mocks = _run("upgrade_charm", legacy, installation="package")

    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)
    assert alloy_ownership.current_owner() == "other-sub/3"


@pytest.mark.parametrize(
    "event_name", ["install", "start", "config_changed", "update_status", "upgrade_charm"]
)
def test_probe_timeout_blocks_the_gate_without_claiming(alloy_marker_dir, event_name):
    state_out, mocks = _run(
        event_name, _legacy(), installation=subprocess.TimeoutExpired(["dpkg-query"], 10)
    )

    assert state_out.unit_status.name == "blocked"
    assert state_out.unit_status.message.startswith("Alloy ownership check failed: ")
    assert "timed out" in state_out.unit_status.message
    assert _marker_bytes(alloy_marker_dir) is None
    _assert_untouched(mocks)


def test_claim_failure_blocks_without_failing_the_hook_and_a_later_run_claims(alloy_marker_dir):
    with patch("charm.alloy_ownership.claim", side_effect=OSError("read-only file system")):
        failed, mocks = _run("upgrade_charm", _legacy(), installation="package")

    assert failed.unit_status == testing.BlockedStatus(
        "Alloy ownership check failed: read-only file system"
    )
    _assert_untouched(mocks)

    state_out, mocks = _run("update_status", failed, installation="package")

    assert alloy_ownership.current_owner() == UNIT
    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    mocks["install"].assert_not_called()
    mocks["restart"].assert_not_called()


def test_owner_recovers_from_a_transient_probe_failure_on_update_status(alloy_marker_dir):
    owned, _ = _run("update_status", _legacy(), installation="package")
    failed, _ = _run("update_status", owned, installation=OSError("probe failed"))
    assert failed.unit_status == testing.BlockedStatus(
        "Alloy ownership check failed: probe failed"
    )

    state_out, mocks = _run("update_status", failed, installation="package")

    assert isinstance(state_out.unit_status, testing.ActiveStatus)
    _assert_untouched(mocks)


def test_install_failure_keeps_maintenance_defers_keeps_the_marker_and_retries(
    alloy_marker_dir,
):
    failed, mocks = _run("install", _installed_here(), installation="none", install_error=APT_DOWN)

    assert failed.unit_status == testing.MaintenanceStatus(f"Installation failed: {APT_DOWN}")
    assert [deferred.name for deferred in failed.deferred] == ["install"]
    assert alloy_ownership.current_owner() == UNIT
    assert _call_names(mocks) == ["install"]

    # The half-finished apt install now shows up as a package; the deferred install finishes it.
    state_out, mocks = _run("update_status", failed, installation="package")

    mocks["install"].assert_called_once()
    mocks["preserve_default_config"].assert_called_once()
    assert _call_names(mocks)[:3] == ["install", "preserve_default_config", "start"]
    assert state_out.deferred == []
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)
    assert alloy_ownership.current_owner() == UNIT


def test_install_is_attempted_once_per_dispatch(alloy_marker_dir):
    failed, _ = _run("install", _installed_here(), installation="none", install_error=APT_DOWN)

    # The deferred install replays first and fails again; update-status must not retry it.
    state_out, mocks = _run(
        "update_status", failed, installation="package", install_error=APT_DOWN
    )

    assert _call_names(mocks) == ["install"]
    assert state_out.unit_status == testing.BlockedStatus(f"Installation failed: {APT_DOWN}")
    assert [deferred.name for deferred in state_out.deferred] == ["install"]


def _blocked_by_other_then_released(installation):
    alloy_ownership.claim("other-sub/3", installed=False)
    blocked, _ = _run("install", testing.State(relations=_relations()), installation=installation)
    assert blocked.unit_status == testing.BlockedStatus(OTHER)
    alloy_ownership.release("other-sub/3")
    return blocked


def _order(mocks, *names):
    return [name for name in _call_names(mocks) if name in names]


def test_blocked_unit_installs_and_configures_once_the_owner_releases(alloy_marker_dir):
    blocked = _blocked_by_other_then_released("none")

    state_out, mocks = _run("update_status", blocked, installation="none")

    names = ("install", "preserve_default_config", "start", "write_config_text")
    assert _order(mocks, *names) == list(names)
    assert alloy_ownership.current_owner() == UNIT
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_blocked_unit_takes_over_a_released_package_alloy(alloy_marker_dir):
    blocked = _blocked_by_other_then_released("package")

    state_out, mocks = _run("update_status", blocked, installation="package")

    names = ("install", "preserve_default_config", "start", "write_config_text")
    assert _order(mocks, *names) == ["preserve_default_config", "start", "write_config_text"]
    assert alloy_ownership.current_owner() == UNIT
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


@pytest.mark.parametrize("event_name", ["config_changed", "machine-observability"])
def test_blocked_unit_takes_over_on_config_and_relation_events(alloy_marker_dir, event_name):
    blocked = _blocked_by_other_then_released("package")

    state_out, mocks = _run(event_name, blocked, installation="package")

    names = ("install", "preserve_default_config", "start", "write_config_text")
    assert _order(mocks, *names) == ["preserve_default_config", "start", "write_config_text"]
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_takeover_whose_start_fails_is_retried_by_the_next_hook(alloy_marker_dir):
    blocked = _blocked_by_other_then_released("package")
    failed, mocks = _run(
        "update_status",
        blocked,
        installation="package",
        side_effects={"start": RuntimeError("alloy did not become active")},
    )

    assert failed.unit_status == testing.BlockedStatus(
        "Installation failed: alloy did not become active"
    )
    mocks["write_config_text"].assert_not_called()
    assert alloy_ownership.current_owner() == UNIT

    state_out, mocks = _run("update_status", failed, installation="package")

    names = ("install", "preserve_default_config", "start", "write_config_text")
    assert _order(mocks, *names) == ["preserve_default_config", "start", "write_config_text"]
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


def test_blocked_unit_taking_over_on_start_writes_its_config(alloy_marker_dir):
    blocked = _blocked_by_other_then_released("package")

    state_out, mocks = _run("start", blocked, installation="package")

    mocks["write_config_text"].assert_called_once()
    mocks["install"].assert_not_called()
    assert not isinstance(state_out.unit_status, testing.BlockedStatus)


# Same fixtures as tests/unit/test_machine_observability_relation.py.
def _rule_artifact(artifact_type, artifact_id, group_name):
    return encode_artifact(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        content=json.dumps(
            {
                "groups": [
                    {
                        "name": group_name,
                        "rules": [{"alert": "Down", "expr": "up{%%juju_topology%%} == 0"}],
                    }
                ]
            }
        ).encode(),
    ).model_dump()


def _v3_payload(*artifacts):
    return {
        "schema_version": 3,
        "source_topology": {
            "model": "principal-model",
            "model_uuid": "principal-uuid",
            "application": "polkadot",
            "unit": "polkadot/0",
            "charm_name": "polkadot",
        },
        "metrics_endpoints": [],
        "systemd_units": [],
        "journal_match_expressions": [],
        "log_files": [],
        "artifacts": list(artifacts),
    }


def test_blocked_leader_still_publishes_alert_rules(alloy_marker_dir, monkeypatch):
    monkeypatch.setattr(AlloyCharm, "_validate_artifact_rules", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(AlloyCharm, "_host_alarms_enabled", lambda _self: False)
    alloy_ownership.claim("other-sub/3", installed=False)
    payload = _v3_payload(_rule_artifact("prometheus_alert_rules", "metrics", "Metrics"))
    remote_write = testing.Relation("send-remote-write", remote_app_name="mimir")
    machine = testing.Relation(
        "machine-observability",
        remote_app_name="polkadot",
        remote_app_data={"payload": json.dumps(payload)},
    )
    state = testing.State(leader=True, relations=[machine, remote_write])

    state_out, mocks = _run("update_status", state, installation="package")

    published = json.loads(state_out.get_relation(remote_write.id).local_app_data["alert_rules"])
    assert len(published["groups"]) == 1
    assert state_out.unit_status == testing.BlockedStatus(OTHER)
    _assert_untouched(mocks)


def test_owner_stop_releases_and_leaves_alloy_running(alloy_marker_dir):
    owned, _ = _run("install", testing.State(), installation="none")
    _, mocks = _run("stop", owned, installation="package")

    mocks["stop"].assert_not_called()
    assert alloy_ownership.current_owner() is None
    assert (alloy_marker_dir / alloy_ownership.MARKER_NAME).read_text() == '{"owner": null}'


def test_non_owner_stop_leaves_alloy_and_marker_alone(alloy_marker_dir):
    alloy_ownership.claim("other-sub/3", installed=False)
    before = _marker_bytes(alloy_marker_dir)
    _, mocks = _run("stop", testing.State(), installation="package")

    mocks["stop"].assert_not_called()
    assert _marker_bytes(alloy_marker_dir) == before
