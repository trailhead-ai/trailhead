"""Tests for trailhead/manifest.py — InstallManifest dataclass + load_install_manifest.

TDD: these tests are written BEFORE the implementation. All must pass after
trailhead/manifest.py is implemented.

Hermeticity contract (the Step-4 lesson):
  Every test that builds/parses manifests uses tmp_path-based TOML strings or
  files. Tests MUST NOT read the real install_manifest.toml from a
  machine-specific location — except the single structural smoke test at the
  bottom which asserts the committed manifest is well-formed (structure only,
  no specific SHA value checked).

Separation (D-1):
  InstallManifest / load_install_manifest is distinct from the capability
  Manifest / load_manifest in capabilities.py. The two manifest types answer
  different questions and must not be conflated.

Security rules covered:
  - S-3: resolved source validated against anchored allowlist; leading '--'
    and shell metacharacters rejected; local paths go through _confine.
  - §1112 / §1115: rev must be exactly 40 lowercase hex chars; tags, short
    SHAs, HEAD, latest → InstallManifestError naming the offending repo.
  - D29: ${registry} resolved against the passed-in registry base; unresolved
    ${registry} (None registry) → named error pointing at 'trailhead config registry'.
  - Duplicate repo entries → error (no last-wins).
  - Missing source / malformed entry → named error citing the file.
"""

from pathlib import Path

import pytest

from trailhead.manifest import InstallManifest, InstallManifestError, load_install_manifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_SHA = "a" * 40  # valid 40-char lowercase hex


def _write_manifest(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "install_manifest.toml"
    p.write_text(content)
    return p


def _minimal_toml(*, repo: str = "trailhead", rev: str = _GOOD_SHA, source: str = "${registry}/trailhead", tools: str = '["trailhead"]') -> str:
    return f"""
[[repo]]
name = "{repo}"
rev = "{rev}"
source = "{source}"
tools = {tools}
"""


# ---------------------------------------------------------------------------
# Happy-path: well-formed manifest
# ---------------------------------------------------------------------------


class TestWellFormedManifest:
    def test_parses_returns_install_manifest(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml())
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert isinstance(result, InstallManifest)

    def test_registry_template_resolves_against_passed_in_registry(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="${registry}/trailhead"))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].source == "https://github.com/trailhead-ai/trailhead"

    def test_fully_qualified_https_source_preserved_as_is(self, tmp_path):
        url = "https://github.com/myorg/trailhead"
        path = _write_manifest(tmp_path, _minimal_toml(source=url))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].source == url

    def test_fully_qualified_git_ssh_source_preserved_as_is(self, tmp_path):
        url = "git@github.com:myorg/trailhead"
        path = _write_manifest(tmp_path, _minimal_toml(source=url))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].source == url

    def test_rev_stored_on_repo_entry(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(rev=_GOOD_SHA))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].rev == _GOOD_SHA

    def test_multiple_repos_all_parsed(self, tmp_path):
        sha2 = "b" * 40
        content = (
            _minimal_toml(repo="trailhead", rev=_GOOD_SHA, source="${registry}/trailhead")
            + f'\n[[repo]]\nname = "other"\nrev = "{sha2}"\nsource = "https://github.com/org/other"\ntools = ["other"]\n'
        )
        path = _write_manifest(tmp_path, content)
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert len(result.repos) == 2
        assert result.repos[1].name == "other"
        assert result.repos[1].rev == sha2

    def test_tools_list_stored_on_repo_entry(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(tools='["lore", "camp"]'))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].tools == ["lore", "camp"]


# ---------------------------------------------------------------------------
# Rev validation: tags / short SHA / HEAD / latest → error
# ---------------------------------------------------------------------------


class TestRevValidation:
    @pytest.mark.parametrize("bad_rev", [
        "v1.0",           # tag
        "v2.3.4",         # version tag
        "abc1234",        # short SHA (7 chars)
        "a" * 12,         # short SHA (12 chars — explicitly called out in spec)
        "HEAD",           # HEAD reference
        "latest",         # latest reference
        "main",           # branch name
        "a" * 39,         # one char short
        "a" * 41,         # one char over
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # uppercase (not lowercase hex)
        "a" * 38 + "gh",  # non-hex chars
    ])
    def test_invalid_rev_raises_install_manifest_error(self, tmp_path, bad_rev):
        path = _write_manifest(tmp_path, _minimal_toml(rev=bad_rev))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        # Error must name the offending repo
        assert "trailhead" in str(exc_info.value)

    def test_error_message_names_the_field(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(rev="v1.0"))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert "rev" in str(exc_info.value)

    def test_valid_40_char_hex_all_lowercase_digits_passes(self, tmp_path):
        sha = "0123456789abcdef" * 2 + "01234567"  # exactly 40 chars
        path = _write_manifest(tmp_path, _minimal_toml(rev=sha))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].rev == sha


# ---------------------------------------------------------------------------
# Missing / malformed entries
# ---------------------------------------------------------------------------


class TestMissingAndMalformedEntries:
    def test_missing_source_raises_install_manifest_error(self, tmp_path):
        content = f'[[repo]]\nname = "trailhead"\nrev = "{_GOOD_SHA}"\ntools = ["trailhead"]\n'
        path = _write_manifest(tmp_path, content)
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert str(path) in str(exc_info.value) or "source" in str(exc_info.value)

    def test_missing_rev_raises_install_manifest_error(self, tmp_path):
        content = '[[repo]]\nname = "trailhead"\nsource = "https://github.com/org/trailhead"\ntools = ["trailhead"]\n'
        path = _write_manifest(tmp_path, content)
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert str(path) in str(exc_info.value) or "rev" in str(exc_info.value)

    def test_missing_name_raises_install_manifest_error(self, tmp_path):
        content = f'[[repo]]\nrev = "{_GOOD_SHA}"\nsource = "https://github.com/org/trailhead"\ntools = ["trailhead"]\n'
        path = _write_manifest(tmp_path, content)
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert str(path) in str(exc_info.value) or "name" in str(exc_info.value)

    def test_malformed_toml_raises_install_manifest_error_citing_file(self, tmp_path):
        path = _write_manifest(tmp_path, "[[repo\nname = trailhead\n")
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert str(path) in str(exc_info.value)

    def test_empty_manifest_raises_install_manifest_error(self, tmp_path):
        path = _write_manifest(tmp_path, "")
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert str(path) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Duplicate repo entries → error (no last-wins)
# ---------------------------------------------------------------------------


class TestDuplicateRepoEntries:
    def test_duplicate_repo_name_raises_install_manifest_error(self, tmp_path):
        content = (
            _minimal_toml(repo="trailhead", rev=_GOOD_SHA)
            + _minimal_toml(repo="trailhead", rev="b" * 40)
        )
        path = _write_manifest(tmp_path, content)
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert "trailhead" in str(exc_info.value)

    def test_no_last_wins_on_duplicate(self, tmp_path):
        content = (
            _minimal_toml(repo="myrepo", rev=_GOOD_SHA)
            + _minimal_toml(repo="myrepo", rev="b" * 40)
        )
        path = _write_manifest(tmp_path, content)
        with pytest.raises(InstallManifestError):
            load_install_manifest(path, registry="https://github.com/trailhead-ai")


# ---------------------------------------------------------------------------
# ${registry} unresolved (no registry configured)
# ---------------------------------------------------------------------------


class TestUnresolvedRegistry:
    def test_registry_none_with_template_source_raises_named_error(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="${registry}/trailhead"))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry=None)
        # Must point user at 'trailhead config registry'
        assert "trailhead config registry" in str(exc_info.value)

    def test_registry_none_with_fully_qualified_source_passes(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="https://github.com/org/trailhead"))
        result = load_install_manifest(path, registry=None)
        assert result.repos[0].source == "https://github.com/org/trailhead"

    def test_registry_empty_string_with_template_source_raises_named_error(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="${registry}/trailhead"))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="")
        assert "trailhead config registry" in str(exc_info.value)


# ---------------------------------------------------------------------------
# S-3: source allowlist validation
# ---------------------------------------------------------------------------


class TestSourceAllowlistValidation:
    def test_source_with_leading_dashes_rejected(self, tmp_path):
        """S-3: a source beginning with '--' injects git options — must be rejected."""
        bad_source = "--upload-pack=/tmp/evil"
        path = _write_manifest(tmp_path, _minimal_toml(source=bad_source))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert "source" in str(exc_info.value).lower() or "trailhead" in str(exc_info.value)

    def test_source_with_shell_metachar_semicolon_rejected(self, tmp_path):
        bad_source = "https://github.com/org/repo;rm -rf /"
        path = _write_manifest(tmp_path, _minimal_toml(source=bad_source))
        with pytest.raises(InstallManifestError):
            load_install_manifest(path, registry="https://github.com/trailhead-ai")

    def test_source_with_shell_metachar_backtick_rejected(self, tmp_path):
        bad_source = "https://github.com/org/repo`evil`"
        path = _write_manifest(tmp_path, _minimal_toml(source=bad_source))
        with pytest.raises(InstallManifestError):
            load_install_manifest(path, registry="https://github.com/trailhead-ai")

    def test_source_with_shell_metachar_pipe_rejected(self, tmp_path):
        bad_source = "https://github.com/org/repo|evil"
        path = _write_manifest(tmp_path, _minimal_toml(source=bad_source))
        with pytest.raises(InstallManifestError):
            load_install_manifest(path, registry="https://github.com/trailhead-ai")

    def test_source_with_shell_metachar_dollar_in_path_rejected(self, tmp_path):
        bad_source = "https://github.com/org/repo$(evil)"
        path = _write_manifest(tmp_path, _minimal_toml(source=bad_source))
        with pytest.raises(InstallManifestError):
            load_install_manifest(path, registry="https://github.com/trailhead-ai")

    def test_valid_https_source_passes(self, tmp_path):
        url = "https://github.com/org/trailhead"
        path = _write_manifest(tmp_path, _minimal_toml(source=url))
        result = load_install_manifest(path, registry=None)
        assert result.repos[0].source == url

    def test_valid_git_ssh_source_passes(self, tmp_path):
        url = "git@github.com:org/trailhead"
        path = _write_manifest(tmp_path, _minimal_toml(source=url))
        result = load_install_manifest(path, registry=None)
        assert result.repos[0].source == url

    def test_resolved_registry_template_producing_valid_https_passes(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="${registry}/trailhead"))
        result = load_install_manifest(path, registry="https://github.com/trailhead-ai")
        assert result.repos[0].source.startswith("https://")

    def test_local_path_confined_to_safe_root(self, tmp_path):
        """S-3 / D-3: local path sources are passed through _confine against tmp_path root."""
        safe_dir = tmp_path / "repos" / "trailhead"
        safe_dir.mkdir(parents=True)
        path = _write_manifest(tmp_path, _minimal_toml(source=str(safe_dir)))
        # A local path inside tmp_path is accepted (no escape)
        result = load_install_manifest(path, registry=None, local_root=tmp_path)
        assert result.repos[0].source == str(safe_dir)

    def test_local_path_escaping_confinement_root_rejected(self, tmp_path):
        """S-3 / D-3: a local path that escapes the confinement root is rejected."""
        escaping_path = str(tmp_path.parent / "escape" / "trailhead")
        path = _write_manifest(tmp_path, _minimal_toml(source=escaping_path))
        with pytest.raises((InstallManifestError, Exception)):
            load_install_manifest(path, registry=None, local_root=tmp_path)


# ---------------------------------------------------------------------------
# L-1: local-self source ("local") — installs the working tree, tracking HEAD
# ---------------------------------------------------------------------------


class TestLocalSelfSource:
    """A source of "local" marks a local-self entry: no pinned rev, no fetch."""

    def _local_toml(self, *, rev_line: str = "") -> str:
        return (
            '[[repo]]\n'
            'name = "trailhead"\n'
            f'{rev_line}'
            'source = "local"\n'
            'tools = ["trailhead", "lore"]\n'
        )

    def test_local_source_marks_entry_local_self_with_no_rev(self, tmp_path):
        path = _write_manifest(tmp_path, self._local_toml())
        result = load_install_manifest(path, registry=None, local_root=tmp_path)
        entry = result.repos[0]
        assert entry.is_local_self is True
        assert entry.rev is None
        assert entry.source == "local"

    def test_remote_entry_is_not_local_self(self, tmp_path):
        path = _write_manifest(tmp_path, _minimal_toml(source="https://github.com/org/trailhead"))
        result = load_install_manifest(path, registry=None)
        assert result.repos[0].is_local_self is False

    def test_local_source_requires_local_root(self, tmp_path):
        """Without a confinement root, 'local' has no defined meaning → refused."""
        path = _write_manifest(tmp_path, self._local_toml())
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry=None, local_root=None)
        assert "local_root" in str(exc_info.value) or "local" in str(exc_info.value).lower()

    def test_local_source_with_rev_is_rejected(self, tmp_path):
        """A 'rev' on a local entry is a parse error (local installs track HEAD)."""
        path = _write_manifest(tmp_path, self._local_toml(rev_line=f'rev = "{_GOOD_SHA}"\n'))
        with pytest.raises(InstallManifestError) as exc_info:
            load_install_manifest(path, registry=None, local_root=tmp_path)
        assert "rev" in str(exc_info.value).lower()

    def test_local_source_does_not_require_registry(self, tmp_path):
        """'local' is not a ${registry} template, so a None registry is fine."""
        path = _write_manifest(tmp_path, self._local_toml())
        result = load_install_manifest(path, registry=None, local_root=tmp_path)
        assert result.repos[0].is_local_self is True


# ---------------------------------------------------------------------------
# Structural smoke test: committed install_manifest.toml is well-formed
# ---------------------------------------------------------------------------


class TestCommittedManifestWellFormed:
    def test_committed_manifest_parses_without_error(self):
        """The committed trailhead/install_manifest.toml must be structurally valid.

        We assert structure only (not specific SHA values, which would churn
        with every commit). The registry is passed in to resolve ${registry}
        templates without requiring real config; local_root is the repo root so
        the local-self ``source = "local"`` entry (L-1) is honored.
        """
        repo_root = Path(__file__).parent.parent.parent
        manifest_path = repo_root / "trailhead" / "install_manifest.toml"
        assert manifest_path.exists(), f"committed manifest not found: {manifest_path}"
        result = load_install_manifest(
            manifest_path, registry="https://github.com/trailhead-ai", local_root=repo_root
        )
        assert isinstance(result, InstallManifest)
        assert len(result.repos) >= 1

    def test_committed_manifest_trailhead_entry_is_local_self(self):
        """The committed trailhead entry is a local-self entry (L-1): source 'local', no rev.

        It installs the working tree, tracking HEAD — there is no blessed SHA to
        pin (self-pinning is circular), so rev must be None.
        """
        repo_root = Path(__file__).parent.parent.parent
        manifest_path = repo_root / "trailhead" / "install_manifest.toml"
        result = load_install_manifest(
            manifest_path, registry="https://github.com/trailhead-ai", local_root=repo_root
        )
        trailhead_entry = next((r for r in result.repos if r.name == "trailhead"), None)
        assert trailhead_entry is not None, "no 'trailhead' entry in committed manifest"
        assert trailhead_entry.is_local_self, "committed trailhead entry must be local-self"
        assert trailhead_entry.rev is None, (
            f"local-self trailhead entry must pin no rev; got {trailhead_entry.rev!r}"
        )
        assert trailhead_entry.source == "local"

    def test_committed_manifest_trailhead_entry_includes_portage_and_landing(self):
        """The trailhead tools list in the committed manifest must include portage and landing.

        Slice 5 registers these two new tools in the trailhead manager.
        """
        repo_root = Path(__file__).parent.parent.parent
        manifest_path = repo_root / "trailhead" / "install_manifest.toml"
        result = load_install_manifest(
            manifest_path, registry="https://github.com/trailhead-ai", local_root=repo_root
        )
        trailhead_entry = next((r for r in result.repos if r.name == "trailhead"), None)
        assert trailhead_entry is not None, "no 'trailhead' entry in committed manifest"
        assert "portage" in trailhead_entry.tools, (
            f"install_manifest.toml trailhead tools list must include 'portage'; "
            f"got: {trailhead_entry.tools}"
        )
        assert "landing" in trailhead_entry.tools, (
            f"install_manifest.toml trailhead tools list must include 'landing'; "
            f"got: {trailhead_entry.tools}"
        )
