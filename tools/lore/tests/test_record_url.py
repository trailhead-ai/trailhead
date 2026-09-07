"""Tests for the record-URL contract module — ``record_url.py``.

Covers the test contract:

- The default-base full URL for a record.
- The URL's vault segment is the resolved, configured vault name — never a
  directory basename (a normalized-name vault proves it).
- Base precedence: ``LORE_RECORD_URL_BASE`` > ``config.json``'s
  ``record_url_base`` > the built-in default.
- An empty ``LORE_RECORD_URL_BASE`` falls through rather than winning.
- A trailing slash on the base does not change the resulting URL.
- A base with no scheme, or an unsupported scheme, is rejected (with the
  rejection surfaced) and the default is used instead.
- Every path segment is percent-encoded.
- ``config.json`` present but lacking ``record_url_base`` falls back to the
  default (a distinct case from no ``config.json`` at all).
- No ``config.json`` present at all still constructs a URL.
- No network access occurs during construction.
"""

import json
import socket

import pytest

from conftest import load_script
# The XDG-env and config.json writers these tests need are the same ones the
# vault-config tests already use; the config layout is one contract, so both
# suites arrange it through one pair of helpers.
from test_vault_config import _make_env, _write_lore_config


def ru():
    return load_script("lore.record_url")


def vc():
    return load_script("lore.vault.config")


# ---------------------------------------------------------------------------
# 1. Default base, full URL
# ---------------------------------------------------------------------------


def test_default_base_produces_expected_url(tmp_path):
    env = _make_env(tmp_path)
    mod = ru()
    result = mod.build_record_url("trailhead", "task", "some-slug", env=env)
    assert result == "http://127.0.0.1:7313/records/trailhead/task/some-slug"


# ---------------------------------------------------------------------------
# 2. Vault segment is the configured name, never the directory basename
# ---------------------------------------------------------------------------


def test_url_uses_configured_name_not_directory_basename(tmp_path):
    directory = tmp_path / "some-other-dirname"
    directory.mkdir()
    env = _make_env(tmp_path)
    cfg = vc()
    config_path = tmp_path / "raw-config.json"
    config_path.write_text(
        json.dumps(
            {
                "vaults": [
                    {
                        "name": "trailhead-ai/trailhead",
                        "scope": "default",
                        "path": str(directory),
                    }
                ]
            }
        )
    )
    vaults = cfg.load_config(str(config_path), env=env)
    vault = vaults[0]
    # Sanity: normalization actually changed the name away from the basename.
    assert vault.name == "trailhead-ai_trailhead"
    assert directory.name != vault.name

    mod = ru()
    result = mod.build_record_url(vault.name, "task", "some-slug", env=env)
    assert "trailhead-ai_trailhead" in result
    assert directory.name not in result


# ---------------------------------------------------------------------------
# 3. Base precedence
# ---------------------------------------------------------------------------


def test_env_base_wins_over_config_base(tmp_path):
    env = _make_env(tmp_path)
    env["LORE_RECORD_URL_BASE"] = "http://env-wins.test:1111"
    _write_lore_config(tmp_path, {"vaults": [], "record_url_base": "http://config-loses.test:2222"})
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://env-wins.test:1111/")


def test_config_base_wins_over_default(tmp_path):
    env = _make_env(tmp_path)
    _write_lore_config(tmp_path, {"vaults": [], "record_url_base": "http://config-wins.test:3333"})
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://config-wins.test:3333/")


def test_builtin_default_is_used_with_no_env_and_no_config(tmp_path):
    env = _make_env(tmp_path)
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://127.0.0.1:7313/")


# ---------------------------------------------------------------------------
# 4. Empty LORE_RECORD_URL_BASE falls through
# ---------------------------------------------------------------------------


def test_empty_env_base_falls_through_to_config(tmp_path):
    env = _make_env(tmp_path)
    env["LORE_RECORD_URL_BASE"] = ""
    _write_lore_config(tmp_path, {"vaults": [], "record_url_base": "http://fallthrough.test:4444"})
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://fallthrough.test:4444/")


# ---------------------------------------------------------------------------
# 5. Trailing slash normalization
# ---------------------------------------------------------------------------


def test_trailing_slash_base_matches_no_trailing_slash(tmp_path):
    env_with_slash = _make_env(tmp_path)
    env_with_slash["LORE_RECORD_URL_BASE"] = "http://example.test:5555/"
    env_without_slash = _make_env(tmp_path)
    env_without_slash["LORE_RECORD_URL_BASE"] = "http://example.test:5555"
    mod = ru()
    with_slash = mod.build_record_url("v", "task", "s", env=env_with_slash)
    without_slash = mod.build_record_url("v", "task", "s", env=env_without_slash)
    assert with_slash == without_slash


# ---------------------------------------------------------------------------
# 6. Scheme rejection
# ---------------------------------------------------------------------------


def test_schemeless_base_rejected_and_default_used(tmp_path):
    env = _make_env(tmp_path)
    env["LORE_RECORD_URL_BASE"] = "127.0.0.1:9999"
    mod = ru()
    with pytest.warns(RuntimeWarning):
        result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://127.0.0.1:7313/")


def test_unsupported_scheme_base_rejected_and_default_used(tmp_path):
    env = _make_env(tmp_path)
    env["LORE_RECORD_URL_BASE"] = "ftp://example.test:6666"
    mod = ru()
    with pytest.warns(RuntimeWarning):
        result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://127.0.0.1:7313/")


def test_scheme_without_netloc_base_rejected_and_default_used(tmp_path):
    """A base with an allowed scheme but no netloc (e.g. ``http:nonsense``,
    which urlsplit parses as scheme "http" with an empty netloc) is rejected
    the same way a missing/unsupported scheme is — never yielding a link like
    ``http:nonsense/records/...``."""
    env = _make_env(tmp_path)
    env["LORE_RECORD_URL_BASE"] = "http:nonsense"
    mod = ru()
    with pytest.warns(RuntimeWarning):
        result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://127.0.0.1:7313/")


# ---------------------------------------------------------------------------
# 7. Percent-encoding of every segment
# ---------------------------------------------------------------------------


def test_segments_are_percent_encoded(tmp_path):
    env = _make_env(tmp_path)
    mod = ru()
    result = mod.build_record_url("my vault", "ta/sk", "slug?../x", env=env)
    assert result == (
        "http://127.0.0.1:7313/records/my%20vault/ta%2Fsk/slug%3F..%2Fx"
    )
    # Structure-altering characters never survive unescaped in the path.
    path = result.split("://", 1)[1].split("/", 1)[1]
    assert path.count("/") == 3  # records / vault / kind / slug — nothing extra


# ---------------------------------------------------------------------------
# 8. config.json present but missing the key falls back to default
# ---------------------------------------------------------------------------


def test_config_present_without_key_falls_back_to_default(tmp_path):
    env = _make_env(tmp_path)
    _write_lore_config(tmp_path, {"vaults": []})
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://127.0.0.1:7313/")


# ---------------------------------------------------------------------------
# 9. No config.json at all still constructs a URL
# ---------------------------------------------------------------------------


def test_no_config_file_at_all_still_constructs_url(tmp_path):
    env = _make_env(tmp_path)
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result == "http://127.0.0.1:7313/records/v/task/s"


# ---------------------------------------------------------------------------
# 10. No network access
# ---------------------------------------------------------------------------


def test_no_network_access_during_construction(tmp_path, monkeypatch):
    env = _make_env(tmp_path)
    _write_lore_config(tmp_path, {"vaults": [], "record_url_base": "http://example.test:7777"})

    def _deny(*args, **kwargs):
        raise AssertionError("record_url must not open a socket")

    monkeypatch.setattr(socket, "socket", _deny)
    mod = ru()
    result = mod.build_record_url("v", "task", "s", env=env)
    assert result.startswith("http://example.test:7777/")
