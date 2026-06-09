"""Slice 1 tests: lore-link-server URI mapping and containment guards.

Tests are pure-function: no live socket, no real `open`, no filesystem I/O
beyond what tmp_path provides. All fixtures are synthetic.

Covers:
- uri_for(request_path, vault_root) maps paths to obsidian://open?path=<abs>
- Trailing .md stripped; nested subfolders preserved; query string ignored
- Port resolution: LORE_LINK_PORT set → int; unset → 7777; invalid → error
- Path traversal containment: /../../etc/passwd is rejected (404, no open)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).parent.parent
BIN_PATH = REPO_ROOT / "plugins" / "lore" / "bin" / "lore-link-server"


def load_server():
    """Load lore-link-server as a module (no-extension filename requires manual load)."""
    loader = SourceFileLoader("lore_link_server", str(BIN_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# URI mapping — uri_for(request_path, vault_root)
# ---------------------------------------------------------------------------

def test_uri_for_simple_path_no_md():
    """uri_for maps /tools/lsp (no .md) to obsidian://open?path=<vault>/tools/lsp.md"""
    mod = load_server()
    result = mod.uri_for("/tools/lsp", "/tmp/v")
    # The path part must encode /tmp/v/tools/lsp.md
    assert result.startswith("obsidian://open?path=")
    assert "/tmp/v/tools/lsp.md" in result.replace("%2F", "/")


def test_uri_for_strips_trailing_md():
    """/tools/lsp.md and /tools/lsp must produce the same URI."""
    mod = load_server()
    without_md = mod.uri_for("/tools/lsp", "/tmp/v")
    with_md = mod.uri_for("/tools/lsp.md", "/tmp/v")
    assert without_md == with_md


def test_uri_for_nested_subfolders_preserved():
    """Nested subfolders are preserved in the resulting absolute path."""
    mod = load_server()
    result = mod.uri_for("/a/b/c", "/tmp/v")
    assert result.startswith("obsidian://open?path=")
    # /tmp/v/a/b/c.md must appear (url-encoded or literal)
    decoded = result.replace("%2F", "/")
    assert "/tmp/v/a/b/c.md" in decoded


def test_uri_for_ignores_query_string():
    """Query string is stripped; path portion only enters the URI."""
    mod = load_server()
    with_q = mod.uri_for("/tools/lsp?foo=bar", "/tmp/v")
    without_q = mod.uri_for("/tools/lsp", "/tmp/v")
    assert with_q == without_q


def test_uri_for_url_encoding():
    """Spaces and special chars in the abs path are percent-encoded in the URI."""
    mod = load_server()
    result = mod.uri_for("/notes/my note", "/tmp/v")
    assert "my%20note" in result


def test_uri_for_encodes_path_with_url_quote():
    """The path= value is percent-encoded per urllib.parse.quote (safe='/')."""
    mod = load_server()
    result = mod.uri_for("/tools/lsp", "/tmp/v")
    # Slash separators should be preserved (safe='/'), not encoded as %2F.
    assert "%2F" not in result
    assert "obsidian://open?path=/tmp/v/tools/lsp.md" == result


# ---------------------------------------------------------------------------
# Port resolution
# ---------------------------------------------------------------------------

def test_port_resolution_env_set():
    """When LORE_LINK_PORT is set to a valid int string, resolve_port returns that int."""
    mod = load_server()
    with mock.patch.dict(os.environ, {"LORE_LINK_PORT": "8888"}, clear=False):
        assert mod.resolve_port() == 8888


def test_port_resolution_env_unset():
    """When LORE_LINK_PORT is not set, resolve_port returns 7777."""
    mod = load_server()
    env = {k: v for k, v in os.environ.items() if k != "LORE_LINK_PORT"}
    with mock.patch.dict(os.environ, env, clear=True):
        assert mod.resolve_port() == 7777


def test_port_resolution_invalid_raises():
    """When LORE_LINK_PORT is a non-int string, resolve_port raises ValueError or SystemExit."""
    mod = load_server()
    with mock.patch.dict(os.environ, {"LORE_LINK_PORT": "notaport"}, clear=False):
        with pytest.raises((ValueError, SystemExit)):
            mod.resolve_port()


def test_port_resolution_empty_string_returns_default():
    """When LORE_LINK_PORT is empty, resolve_port returns 7777."""
    mod = load_server()
    with mock.patch.dict(os.environ, {"LORE_LINK_PORT": ""}, clear=False):
        assert mod.resolve_port() == 7777


# ---------------------------------------------------------------------------
# Path traversal containment
# ---------------------------------------------------------------------------

def test_path_traversal_rejected(tmp_path):
    """A request path like /../../etc/passwd must not resolve outside vault_root."""
    mod = load_server()
    vault_root = str(tmp_path / "vault")
    os.makedirs(vault_root, exist_ok=True)
    # is_contained returns False for traversal attempts
    assert not mod.is_contained("/../../etc/passwd", vault_root)


def test_path_traversal_normal_path_accepted(tmp_path):
    """A legitimate sub-path is accepted by is_contained."""
    mod = load_server()
    vault_root = str(tmp_path / "vault")
    os.makedirs(vault_root, exist_ok=True)
    assert mod.is_contained("/tools/lsp", vault_root)


def test_path_traversal_nested_accepted(tmp_path):
    """A nested sub-path is accepted by is_contained."""
    mod = load_server()
    vault_root = str(tmp_path / "vault")
    os.makedirs(vault_root, exist_ok=True)
    assert mod.is_contained("/a/b/c", vault_root)


def test_path_traversal_exact_vault_root_accepted(tmp_path):
    """The vault root itself is accepted (edge: empty relative path)."""
    mod = load_server()
    vault_root = str(tmp_path / "vault")
    os.makedirs(vault_root, exist_ok=True)
    # A request for "/" maps to the vault root itself, which is contained
    assert mod.is_contained("/", vault_root)


def test_path_traversal_null_byte_fails_closed(tmp_path):
    """An embedded null byte makes realpath raise ValueError; the guard must
    fail closed (return False, no traceback escaping) rather than crash do_GET."""
    mod = load_server()
    vault_root = str(tmp_path / "vault")
    os.makedirs(vault_root, exist_ok=True)
    assert not mod.is_contained("/notes/foo\x00bar", vault_root)
