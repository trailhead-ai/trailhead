"""Full-tree plural→singular taxonomy audit + vault `.gitignore` scaffolding.

lore vault directories are **singular**. This module greps the lore plugin tree
for **path-like** plural kind-dir literals — i.e. directory references, not field
or kind *names* — and asserts that singular is the only convention, with a small,
explicitly-documented allowlist for the legitimate plural survivors classified as
keep-and-track.

Two path-like shapes are flagged (these are how a *directory* is referenced in
this codebase):

  1. ``Path(vault) / "<plural>"``  — a path-join onto a plural dir name.
  2. ``"<plural>/"``               — a trailing-slash prefix literal (e.g. the
     wikilink slug-prefix tuple in ``frontmatter.py``).

A bare ``"<plural>"`` with no slash and no path-join is NOT flagged: those are
record *kind* names, frontmatter *field* names, or CLI *subcommand* names — a
different axis from directory layout (e.g. the ``lore areas`` subcommand, the
``areas`` frontmatter overlap-key, ``record_model``'s ``collaboration`` kind).

The companion test asserts the installer scaffolds a vault ``.gitignore`` that
ignores ``*.lock`` (the flock sidecars `lore sync`'s ``git add -A`` would
otherwise commit). Uses ``tmp_path`` only — never the real vault (Axiom 6).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
PLUGIN_ROOT = TESTS_DIR.parent / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"

sys.path.insert(0, str(TESTS_DIR))
from conftest import load_script  # noqa: E402


# The plural kind-dir names whose path-like use signals legacy taxonomy.
_PLURAL_DIRS = (
    "sessions",
    "areas",
    "decisions",
    "lessons",
    "specs",
    "plans",
    "deferred",
    "dead-ends",
    "follow-ups",
    "designs",
    "gotchas",
    "reviews",
    "audits",
    "briefings",
)

# Path-like usages of a plural dir name:
#   (1) a path-join:  / "<plural>"   (optionally with a trailing slash inside)
#   (2) a prefix lit: "<plural>/"    (trailing slash inside the quotes)
_PATH_JOIN_RE = re.compile(
    r'/\s*"(' + "|".join(re.escape(d) for d in _PLURAL_DIRS) + r')/?"'
)
_PREFIX_LIT_RE = re.compile(
    r'"(' + "|".join(re.escape(d) for d in _PLURAL_DIRS) + r')/"'
)


# ---------------------------------------------------------------------------
# Allowlist — legitimate plural-dir survivors, each with a documented WHY.
#
# Every entry is (relative_path, reason). A flagged line in an allowlisted file
# is tolerated; nothing else is. Keep this list TIGHT — when a survivor is
# retired, delete its entry so the audit re-asserts singular across the freed
# surface.
#
# The legacy plural-taxonomy survivors that were once kept-and-tracked
# (follow_up_due.py, migrate_radar_to_follow_ups.py, migrate_vault.py and the
# `deferred`/`follow-up`/`dead-end` status keys) were RETIRED by the
# retire-legacy-plural-taxonomy-survivors follow-up — their files are deleted and
# the status keys are gone from CANONICAL — so the allowlist is now empty and the
# audit asserts singular across the whole tree.
_ALLOWLIST: dict[str, str] = {}


def _code_files():
    """Yield every lore-plugin code file the audit scans (.py + cli/lore + .sh)."""
    for p in SCRIPTS_DIR.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
    # The lore/lore/ package tree — modules migrated off scripts/ by the
    # ongoing lore/camp packaging refactor. Without this, every migrated
    # module silently drops out of the audit instead of being re-verified
    # under its new path.
    for p in (PLUGIN_ROOT / "lore").rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p
    yield CLI_PATH
    for p in HOOKS_DIR.rglob("*"):
        if p.is_file() and p.suffix in (".sh", ".py"):
            yield p


def _plural_path_hits(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, line) for every path-like plural-dir literal in *path*."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if _PATH_JOIN_RE.search(line) or _PREFIX_LIT_RE.search(line):
            hits.append((i, line.strip()))
    return hits


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

class TestSingularDirAudit:
    def test_no_unallowlisted_plural_dir_literals(self):
        """Singular is the only directory convention outside the documented allowlist."""
        violations: list[str] = []
        for path in _code_files():
            rel = str(path.relative_to(PLUGIN_ROOT))
            if rel in _ALLOWLIST:
                continue
            for lineno, line in _plural_path_hits(path):
                violations.append(f"{rel}:{lineno}: {line}")

        assert not violations, (
            "Plural kind-dir path literals found outside the allowlist — "
            "singularize them or, if a legitimate survivor, add a documented "
            "allowlist entry explaining WHY:\n  " + "\n  ".join(violations)
        )

    def test_allowlist_entries_are_real_and_still_plural(self):
        """Each allowlist entry must name an existing file that still has a plural hit.

        Guards against a stale allowlist: if a survivor was later singularized or
        deleted, its entry should be removed so the allowlist stays honest.
        """
        for rel in _ALLOWLIST:
            path = PLUGIN_ROOT / rel
            assert path.exists(), f"allowlisted file missing: {rel}"
            assert _plural_path_hits(path), (
                f"allowlist entry {rel} must not have a plural path literal — "
                "remove the stale entry."
            )

    def test_frontmatter_slug_prefixes_are_singular(self):
        """frontmatter._SLUG_PREFIXES is singularized."""
        fm = load_script("lore.search.frontmatter")
        assert "area/" in fm._SLUG_PREFIXES
        assert "plan/" in fm._SLUG_PREFIXES
        assert "areas/" not in fm._SLUG_PREFIXES
        assert "plans/" not in fm._SLUG_PREFIXES


# ---------------------------------------------------------------------------
# Vault .gitignore scaffolding
# ---------------------------------------------------------------------------

class TestVaultGitignoreScaffolding:
    def test_fresh_vault_ignores_lock_files(self, tmp_path):
        """A freshly-initialized vault scaffolds a .gitignore ignoring *.lock.

        `lore sync`'s `git add -A` would otherwise commit the session/<key>.lock
        flock sidecars. Uses tmp_path only — never the real vault (Axiom 6).
        """
        installer = load_script("installer")
        vaults_root = tmp_path / "vaults"
        vault = installer.bootstrap_vault(vaults_root, vault_path=None)

        gitignore = vault / ".gitignore"
        assert gitignore.is_file(), "vault .gitignore was not scaffolded"
        patterns = gitignore.read_text(encoding="utf-8").splitlines()
        assert "*.lock" in patterns, (
            f"*.lock not ignored by scaffolded .gitignore: {patterns!r}"
        )

    def test_lock_file_is_git_ignored_in_fresh_vault(self, tmp_path):
        """The scaffolded ignore actually causes git to ignore a *.lock file.

        Uses --ignored=matching so git reports individual ignored files rather
        than collapsing a directory of only-ignored files into '!! dir/' — the
        matching mode reports each file even when its parent dir is otherwise
        empty.
        """
        import subprocess

        installer = load_script("installer")
        vaults_root = tmp_path / "vaults"
        vault = installer.bootstrap_vault(vaults_root, vault_path=None)

        (vault / "session").mkdir(parents=True, exist_ok=True)
        lock = vault / "session" / "deadbeef.lock"
        lock.write_text("", encoding="utf-8")

        result = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain", "--ignored=matching"],
            capture_output=True,
            text=True,
        )
        # The lock file must appear as ignored (!!), never as untracked (??).
        assert "?? session/deadbeef.lock" not in result.stdout, (
            f"lock file is tracked-as-untracked, not ignored:\n{result.stdout}"
        )
        assert "!! session/deadbeef.lock" in result.stdout, (
            f"lock file is not reported ignored by git:\n{result.stdout}"
        )

    def test_bootstrap_is_idempotent_on_gitignore(self, tmp_path):
        """Re-running bootstrap_vault does not duplicate or clobber the .gitignore."""
        installer = load_script("installer")
        vaults_root = tmp_path / "vaults"
        vault = installer.bootstrap_vault(vaults_root, vault_path=None)
        first = (vault / ".gitignore").read_text(encoding="utf-8")
        installer.bootstrap_vault(vaults_root, vault_path=None)
        second = (vault / ".gitignore").read_text(encoding="utf-8")
        assert first == second
        assert "*.lock" in second.splitlines()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
