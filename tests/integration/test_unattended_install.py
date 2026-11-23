"""Validate an explicitly selected disposable local recovery deployment."""

import os
import time
import urllib.request

import jubilant
import pytest


@pytest.mark.skipif(not os.getenv("ALLOY_RECOVERY_TEST_MODEL"), reason="requires local test model")
def test_installer_artifact_and_listener_config():
    model = os.environ["ALLOY_RECOVERY_TEST_MODEL"]
    assert model.startswith("local:admin/celo-alloy-recovery-test-")
    juju = jubilant.Juju(model=model)
    juju.wait(jubilant.all_active, timeout=60)
    status = juju.status()
    app = status.apps["alloy-vm"]
    address = app.units["alloy-vm/0"].public_address
    previous = juju.config("alloy-vm")["custom_args"]
    try:
        juju.config("alloy-vm", {"custom_args": "--server.http.listen-addr=0.0.0.0:6988"})
        deadline = time.monotonic() + 45
        while True:
            try:
                with urllib.request.urlopen(
                    f"http://{address}:6988/-/ready", timeout=2
                ) as response:
                    assert response.status == 200
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
    finally:
        juju.config("alloy-vm", {"custom_args": previous})
    juju.wait(jubilant.all_active, timeout=60)
