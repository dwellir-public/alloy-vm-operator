"""Shared unit-test fixtures."""

import sys

import pytest


@pytest.fixture(autouse=True)
def alloy_marker_dir(request, tmp_path, monkeypatch):
    """Isolate the ownership marker; pre-existing tests run as the owning unit.

    Any second copy of the module tree (imported as ``src.charm``) is patched too.
    """
    import alloy_ownership
    import charm

    for ownership in {alloy_ownership, sys.modules.get("src.alloy_ownership")} - {None}:
        monkeypatch.setattr(ownership, "MARKER_DIR", tmp_path / "juju-alloy")
    if "real_ownership" not in request.keywords:
        for charm_module in {charm, sys.modules.get("src.charm")} - {None}:
            monkeypatch.setattr(
                charm_module.AlloyCharm, "_ensure_alloy_ownership", lambda self, **_kwargs: True
            )
            monkeypatch.setattr(
                charm_module.alloy_ownership, "release", lambda *_args, **_kwargs: True
            )
            monkeypatch.setattr(charm_module.AlloyCharm, "_owns_alloy", lambda self: True)
    return tmp_path / "juju-alloy"
