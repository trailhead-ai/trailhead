"""Behavioral proof that ``host_name`` degrades gracefully instead of raising
when camp's own lazy ``import trailhead.paths`` runs before trailhead has
been made importable.

The bug: ``_camp_self_host_name`` imports ``camp.host.config`` and calls its
``self_host_name`` without first calling ``_bootstrap.ensure_trailhead_importable()``
— the guard ``vault/layers.py`` already applies around its OWN camp import, for
the same reason (camp lazily imports ``trailhead.paths`` internally). This
venv has trailhead pip-installed in editable mode, so ``trailhead.paths`` is
importable ambiently regardless of bootstrap — a plain call here can't observe
the crash. Faking ``_bootstrap`` and ``camp.host.config`` instead lets the test
see the property that is actually load-bearing when trailhead genuinely is not
yet on ``sys.path``: whether the bootstrap call happens BEFORE the camp call.
"""
from __future__ import annotations

import sys
import types

import lore.cli.common as common


def test_camp_self_host_name_bootstraps_trailhead_before_using_camp(monkeypatch):
    bootstrap_calls: list[bool] = []

    fake_bootstrap = types.ModuleType("_bootstrap")
    fake_bootstrap.ensure_trailhead_importable = lambda: bootstrap_calls.append(True)
    monkeypatch.setitem(sys.modules, "_bootstrap", fake_bootstrap)

    class _HostConfigError(Exception):
        pass

    def _fake_self_host_name(env):
        if not bootstrap_calls:
            # The real failure mode: camp's own lazy `import trailhead.paths`
            # raising because nothing made it importable first.
            raise ModuleNotFoundError("No module named 'trailhead.paths'")
        return "camp-declared-name"

    fake_camp = types.ModuleType("camp")
    fake_camp_host = types.ModuleType("camp.host")
    fake_camp_host_config = types.ModuleType("camp.host.config")
    fake_camp_host_config.HostConfigError = _HostConfigError
    fake_camp_host_config.self_host_name = _fake_self_host_name
    monkeypatch.setitem(sys.modules, "camp", fake_camp)
    monkeypatch.setitem(sys.modules, "camp.host", fake_camp_host)
    monkeypatch.setitem(sys.modules, "camp.host.config", fake_camp_host_config)

    result = common._camp_self_host_name(env={})

    assert result == "camp-declared-name"
    assert bootstrap_calls, "ensure_trailhead_importable must run before camp is used"
