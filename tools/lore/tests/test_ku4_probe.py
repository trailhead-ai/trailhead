"""KU4 assumption probe: live vs dead plural legacy surfaces.

Verifies per-surface which surfaces are LIVE (need singularize/repair) vs DEAD
(safe to retire). Run as part of the normal pytest suite; ephemeral — clean up
after Slice 7 is implemented.

Surfaces probed:
  1. TAXONOMY constant in cli/lore
  2. recall.py uses areas/ (plural) — lore areas command live via recall_mod
  3. regenerate_indices.py JOBS dirs and pre-commit hook wiring
  4. status_validator.py remaining plural keys
  5. Additional plural-dir literals across the lore plugin tree
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "lore"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
CLI_PATH = PLUGIN_ROOT / "cli" / "lore"
HOOKS_DIR = PLUGIN_ROOT / "hooks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grep(path: Path, pattern: str) -> list[str]:
    """Return lines in path matching pattern (simple substring)."""
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if pattern in l]


def _grep_dir(directory: Path, pattern: str, exts: tuple[str, ...] = (".py", "")) -> list[tuple[Path, str]]:
    """Return (file, line) pairs matching pattern across directory."""
    hits = []
    for p in directory.rglob("*"):
        if p.is_file() and (not exts or any(p.suffix == e or (e == "" and p.stem == p.name) for e in exts)):
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if pattern in line:
                        hits.append((p, line))
            except Exception:
                pass
    return hits


def _load_script(name: str) -> object:
    """Load a scripts/*.py module freshly."""
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_cli_constant(name: str) -> object:
    """Extract a top-level constant from cli/lore by exec-ing just the header."""
    # We only read it as text and parse the TAXONOMY list directly.
    text = CLI_PATH.read_text(encoding="utf-8")
    # Find TAXONOMY = [...] block
    m = re.search(r"^TAXONOMY\s*=\s*\[([^\]]*)\]", text, re.MULTILINE | re.DOTALL)
    if m:
        items = re.findall(r'"([^"]+)"', m.group(1))
        return items
    return []


# ---------------------------------------------------------------------------
# Surface 1: TAXONOMY in cli/lore — is it read by any caller?
# ---------------------------------------------------------------------------

class TestSurface1TaxonomyDead:
    """TAXONOMY at cli/lore:47 — confirm no callers read it."""

    def test_taxonomy_defined_in_cli(self):
        """TAXONOMY constant exists at line ~47."""
        lines = _grep(CLI_PATH, "TAXONOMY")
        assert any("TAXONOMY" in l for l in lines), "TAXONOMY definition not found"

    def test_taxonomy_not_referenced_beyond_definition(self):
        """TAXONOMY is defined once and never read — safe to delete."""
        cli_text = CLI_PATH.read_text(encoding="utf-8")
        # Count occurrences of TAXONOMY (the definition itself + any reads)
        hits = re.findall(r"\bTAXONOMY\b", cli_text)
        # Only one occurrence = the definition line itself
        assert len(hits) == 1, (
            f"TAXONOMY appears {len(hits)} times in cli/lore — expected 1 (definition only). "
            f"Extra hits mean it has callers and is NOT dead."
        )

    def test_taxonomy_not_referenced_in_scripts(self):
        """No script imports or reads TAXONOMY from cli/lore."""
        for p in SCRIPTS_DIR.rglob("*.py"):
            text = p.read_text(encoding="utf-8")
            assert "TAXONOMY" not in text, (
                f"{p.name} references TAXONOMY — it is NOT dead"
            )

    def test_taxonomy_not_referenced_in_tests(self):
        """No test references TAXONOMY."""
        tests_dir = REPO_ROOT / "tests"
        for p in tests_dir.rglob("*.py"):
            if p.name == "test_ku4_probe.py":
                continue
            text = p.read_text(encoding="utf-8")
            assert "TAXONOMY" not in text, (
                f"{p.name} references TAXONOMY — it is NOT dead"
            )

    def test_taxonomy_content_is_plural_legacy(self):
        """TAXONOMY contains plural dir names — none match the S1 singular vocab."""
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        taxonomy = _load_cli_constant("TAXONOMY")
        assert taxonomy, "TAXONOMY is empty or could not be parsed"
        # These are the plural dirs that should NOT exist in a singular S1 vault
        plural_kinds = {"sessions", "areas", "decisions", "lessons", "dead-ends",
                        "follow-ups", "specs", "plans", "deferred"}
        overlap = set(taxonomy) & plural_kinds
        assert overlap, (
            f"TAXONOMY doesn't contain any plural kinds — investigation assumption may be wrong: {taxonomy}"
        )


# ---------------------------------------------------------------------------
# Surface 2: recall.py uses areas/ (plural) — LIVE (lore areas is wired to it)
# ---------------------------------------------------------------------------

class TestSurface2RecallAreasPlural:
    """recall.py scans areas/ (plural) but vault uses area/ (singular) — LIVE defect."""

    def test_recall_scans_areas_plural(self):
        """build_area_map hardcodes vault/'areas' (plural) path."""
        recall_src = (SCRIPTS_DIR / "recall.py").read_text()
        assert '"areas"' in recall_src or "/ \"areas\"" in recall_src or "/ 'areas'" in recall_src or 'vault / "areas"' in recall_src, (
            "recall.py does not use areas/ (plural) — assumption is wrong"
        )

    def test_recall_imported_by_cli(self):
        """cli/lore imports recall and calls build_area_map for lore areas command."""
        cli_text = CLI_PATH.read_text()
        assert "import recall as recall_mod" in cli_text, "cli/lore does not import recall"
        assert "recall_mod.build_area_map" in cli_text, "cli/lore does not call build_area_map"

    def test_recall_used_in_live_cmd_areas(self):
        """cmd_areas in cli/lore calls recall_mod.build_area_map — not dead."""
        cli_text = CLI_PATH.read_text()
        # cmd_areas must be a function + call build_area_map
        assert "def cmd_areas" in cli_text, "cmd_areas not found in cli"
        assert "recall_mod.build_area_map(vault)" in cli_text, (
            "cmd_areas does not call build_area_map — recall may be dead after all"
        )

    def test_areas_plural_dir_absent_in_live_vault_singular_area_present(self, tmp_path):
        """build_area_map with a singular area/ dir returns empty (wrong dir scanned).

        This proves the defect: the live vault uses area/ but recall.py scans areas/.
        """
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        # Load recall freshly
        for cached in ("recall", "frontmatter"):
            sys.modules.pop(cached, None)
        spec = importlib.util.spec_from_file_location("recall", SCRIPTS_DIR / "recall.py")
        recall_mod = importlib.util.module_from_spec(spec)
        sys.modules["recall"] = recall_mod
        spec.loader.exec_module(recall_mod)

        # Create a fake vault with SINGULAR area/ dir containing a profile
        vault = tmp_path / "vault"
        singular_dir = vault / "area"  # correct S1 dir
        singular_dir.mkdir(parents=True)
        (singular_dir / "my-area.md").write_text(
            "---\nname: my-area\nsummary: A test area\n---\n\n## Overview\nArea overview.\n"
        )

        # build_area_map scans areas/ (plural) — should return [] since only area/ exists
        entries = recall_mod.build_area_map(vault)
        assert entries == [], (
            f"build_area_map returned {entries} — it must be scanning singular area/ "
            "which would be correct; verify it still scans areas/ (plural)"
        )

    def test_areas_plural_dir_works_in_recall(self, tmp_path):
        """build_area_map finds entries when areas/ (plural) exists — confirming plural scan."""
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        for cached in ("recall", "frontmatter"):
            sys.modules.pop(cached, None)
        spec = importlib.util.spec_from_file_location("recall", SCRIPTS_DIR / "recall.py")
        recall_mod = importlib.util.module_from_spec(spec)
        sys.modules["recall"] = recall_mod
        spec.loader.exec_module(recall_mod)

        # Create a vault with PLURAL areas/ dir
        vault = tmp_path / "vault"
        plural_dir = vault / "areas"  # legacy plural dir
        plural_dir.mkdir(parents=True)
        (plural_dir / "my-area.md").write_text(
            "---\nname: my-area\nsummary: A test area\n---\n\n## Overview\nArea overview.\n"
        )

        entries = recall_mod.build_area_map(vault)
        assert len(entries) == 1, (
            f"build_area_map did not find the entry in areas/ — got {entries}"
        )
        assert entries[0].name == "my-area"


# ---------------------------------------------------------------------------
# Surface 3: regenerate_indices.py — JOBS dirs, hook wiring, S1 relevance
# ---------------------------------------------------------------------------

class TestSurface3RegenerateIndices:
    """regenerate_indices.py JOBS reference pre-S1 dirs absent from S1 vault."""

    def test_jobs_dirs_do_not_exist_in_live_vault(self):
        """JOBS dirs (deferred/, follow-ups/, lessons/, plans/, specs/, designs/)
        are NOT present in the live vault — only singular S1 kinds exist there."""
        live_vault = Path("/Users/tduffield/.local/state/lore/vaults/default")
        if not live_vault.exists():
            import pytest
            pytest.skip("Live vault not accessible in this environment")

        # Load regenerate_indices to get JOBS
        regen = _load_script("regenerate_indices")
        jobs_folder_names = [folder_name for folder_name, _, _ in regen.JOBS]

        live_dirs = {p.name for p in live_vault.iterdir() if p.is_dir()}

        # None of the JOBS dirs should be in the live vault
        jobs_present = set(jobs_folder_names) & live_dirs
        assert not jobs_present, (
            f"JOBS dirs present in live vault: {jobs_present} — "
            f"regenerate_indices.py is LIVE (has data to process)"
        )

    def test_pre_commit_hook_script_wires_regen_indices(self):
        """install-vault-hooks.sh wires pre-commit-regen-indices.sh — hook is live."""
        installer_sh = HOOKS_DIR / "install-vault-hooks.sh"
        text = installer_sh.read_text()
        assert "pre-commit-regen-indices.sh" in text, (
            "install-vault-hooks.sh does not wire the regen indices hook"
        )
        assert "REGEN_SH" in text, "Regen hook is not referenced in installer"

    def test_regen_hook_calls_regenerate_indices_py(self):
        """pre-commit-regen-indices.sh calls regenerate_indices.py."""
        hook_sh = HOOKS_DIR / "pre-commit-regen-indices.sh"
        text = hook_sh.read_text()
        assert "regenerate_indices.py" in text, (
            "pre-commit-regen-indices.sh does not invoke regenerate_indices.py"
        )

    def test_regen_hook_exists_in_hooks_dir(self):
        """Both hook scripts exist."""
        assert (HOOKS_DIR / "pre-commit-regen-indices.sh").exists()
        assert (HOOKS_DIR / "pre-commit-status-guard.sh").exists()

    def test_regen_tests_count(self):
        """~4 test files reference regenerate_indices (as stated in plan KU4)."""
        tests_dir = REPO_ROOT / "tests"
        referencing = []
        for p in tests_dir.rglob("*.py"):
            if "regenerate_indices" in p.read_text(encoding="utf-8"):
                referencing.append(p.name)
        assert len(referencing) >= 1, (
            f"No test files reference regenerate_indices — expected at least 1, got: {referencing}"
        )
        # Surface how many there are
        assert len(referencing) <= 10, (
            f"Unexpectedly many test files reference regenerate_indices: {referencing}"
        )

    def test_regen_jobs_are_all_plural_dirs(self):
        """Every JOBS folder_name is a plural/legacy dir name, not a singular S1 kind."""
        regen = _load_script("regenerate_indices")
        singular_kinds = {"area", "backlog", "blob", "collaboration",
                          "decision", "lesson", "plan", "session", "spec"}
        jobs_folder_names = [folder_name for folder_name, _, _ in regen.JOBS]
        # None of the JOBS dirs should be a singular S1 kind
        singular_in_jobs = set(jobs_folder_names) & singular_kinds
        assert not singular_in_jobs, (
            f"Some JOBS dirs overlap with singular S1 kinds: {singular_in_jobs} — "
            "regenerate_indices.py may have been partially updated already"
        )


# ---------------------------------------------------------------------------
# Surface 4: status_validator.py remaining plural keys
# ---------------------------------------------------------------------------

class TestSurface4StatusValidatorPluralKeys:
    """status_validator.py CANONICAL — identify remaining plural/legacy keys."""

    def _load_sv(self):
        sys.modules.pop("status_validator", None)
        spec = importlib.util.spec_from_file_location(
            "status_validator", SCRIPTS_DIR / "status_validator.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["status_validator"] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_session_is_singular_key(self):
        """session key is singular (done in Slice 0)."""
        sv = self._load_sv()
        assert "session" in sv.CANONICAL, "session key missing from CANONICAL"
        assert "sessions" not in sv.CANONICAL, "sessions (plural) still in CANONICAL"

    def test_remaining_plural_keys_identified(self):
        """Enumerate remaining plural/legacy keys in CANONICAL for KU4 evidence."""
        sv = self._load_sv()
        known_plural = {"plans", "specs", "follow-ups", "dead-ends", "lessons"}
        remaining_plural = set(sv.CANONICAL.keys()) & known_plural
        # These should all still be present (pre-S7 state)
        assert "plans" in sv.CANONICAL, "plans key missing"
        assert "specs" in sv.CANONICAL, "specs key missing"
        assert "follow-ups" in sv.CANONICAL, "follow-ups key missing"
        assert "dead-ends" in sv.CANONICAL, "dead-ends key missing"
        assert "lessons" in sv.CANONICAL, "lessons key missing"
        assert "deferred" in sv.CANONICAL, "deferred key missing"

    def test_deferred_key_is_already_singular(self):
        """deferred key in CANONICAL is the singular form — no plural 'deferreds'."""
        sv = self._load_sv()
        assert "deferred" in sv.CANONICAL, "deferred (singular) missing"
        # Already singular — but used as a plural dir name in regenerate_indices JOBS
        # The dir is deferred/ which is a plural-style dir for a singular concept
        assert sv.CANONICAL["deferred"] == frozenset(
            {"open", "scheduled", "resolved", "dropped", "graduated", "resurfaced"}
        )

    def test_sv_plural_keys_map_to_absent_vault_kinds(self):
        """Plans/specs/follow-ups/lessons/dead-ends are NOT S1 record kinds.

        These are legacy plural dir names for kinds that exist in the S1 vault
        under singular names (plan/, spec/, lesson/, decision/) OR don't exist as
        S1 kinds at all (follow-ups/, dead-ends/).
        """
        sv = self._load_sv()
        # Load S1 record model kinds
        sys.modules.pop("record_model", None)
        spec = importlib.util.spec_from_file_location(
            "record_model", SCRIPTS_DIR / "record_model.py"
        )
        rm = importlib.util.module_from_spec(spec)
        sys.modules["record_model"] = rm
        spec.loader.exec_module(rm)

        singular_kinds = rm.KINDS

        # These CANONICAL keys are plural dir names — none match S1 singular kinds
        plural_validator_keys = {"plans", "specs", "follow-ups", "dead-ends", "lessons"}
        for key in plural_validator_keys:
            assert key not in singular_kinds, (
                f"Plural validator key {key!r} IS a singular S1 kind — unexpected"
            )

    def test_pre_commit_status_guard_calls_status_validator(self):
        """pre-commit-status-guard.sh invokes status_validator.py — sv is LIVE."""
        guard_sh = HOOKS_DIR / "pre-commit-status-guard.sh"
        text = guard_sh.read_text()
        assert "status_validator.py" in text, (
            "pre-commit-status-guard.sh does not call status_validator.py — it may be dead"
        )

    def test_follow_up_due_uses_status_validator(self):
        """follow_up_due.py imports status_validator (indirectly via CANONICAL)."""
        fu_src = (SCRIPTS_DIR / "follow_up_due.py").read_text()
        # follow_up_due uses status_validator's CANONICAL["follow-ups"] indirectly
        # by comparing against hardcoded statuses from that set
        assert "status_validator" in fu_src or "_CLOSED_STATUSES" in fu_src, (
            "follow_up_due.py does not reference status_validator or its vocab — "
            "verify the linkage"
        )

    def test_follow_up_due_uses_follow_ups_plural_dir(self):
        """follow_up_due.py reads from follow-ups/ (plural) dir — LIVE plural reference."""
        fu_src = (SCRIPTS_DIR / "follow_up_due.py").read_text()
        assert '"follow-ups"' in fu_src or "/ 'follow-ups'" in fu_src, (
            "follow_up_due.py does not reference follow-ups/ dir — assumption may be wrong"
        )


# ---------------------------------------------------------------------------
# Surface 5: Additional plural-dir literals — classify hits
# ---------------------------------------------------------------------------

class TestSurface5AdditionalPluralLiterals:
    """Broad grep for plural-dir strings across the lore plugin tree."""

    PLURAL_DIRS = [
        "sessions/", "areas/", "decisions/", "lessons/",
        "deferred/", "dead-ends/", "follow-ups/", "specs/", "plans/",
    ]

    def _scan_plugin_for(self, pattern: str) -> list[tuple[str, str]]:
        """Return (relative_path, line) for pattern hits in the plugin tree."""
        hits = []
        for p in PLUGIN_ROOT.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix in (".pyc", ".db", ".lock") or "__pycache__" in str(p):
                continue
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if pattern in line:
                        rel = str(p.relative_to(PLUGIN_ROOT))
                        hits.append((rel, line.strip()))
            except Exception:
                pass
        return hits

    def test_sessions_plural_in_code_is_backward_compat_only(self):
        """sessions/ references in code are for back-compat reads, not new writes."""
        hits = self._scan_plugin_for("sessions/")
        code_hits = [(f, l) for f, l in hits if f.endswith(".py")]
        # All sessions/ code hits should be in vault.py (back-compat reader) or
        # session_store.py (docstring/comment), not in write paths
        suspicious = [
            (f, l) for f, l in code_hits
            if "sessions/" in l
            and not any(comment_marker in l for comment_marker in ("#", '"""', "'''", "``"))
            and "write" not in l.lower()
            and "append" not in l.lower()
        ]
        # vault.py has the back-compat plural sessions/ reader — that's expected
        session_write_hits = [
            (f, l) for f, l in code_hits
            if "sessions/" in l and ("write" in l.lower() or "mkdir" in l.lower() or "open" in l.lower())
        ]
        assert not session_write_hits, (
            f"Code still WRITES to sessions/ — not back-compat read-only:\n" +
            "\n".join(f"  {f}: {l}" for f, l in session_write_hits)
        )

    def test_vault_py_has_plural_sessions_back_compat_reader(self):
        """vault.py retains find_session_note (plural sessions/) for back-compat."""
        vault_src = (SCRIPTS_DIR / "vault.py").read_text()
        assert "sessions" in vault_src, "vault.py no longer references sessions dir"
        assert "def find_session_note(" in vault_src, (
            "find_session_note (plural sessions/ reader) not in vault.py"
        )
        assert "def find_session_note_by_session_id(" in vault_src, (
            "find_session_note_by_session_id not in vault.py"
        )

    def test_recall_py_areas_plural_is_only_code_plural_areas_hit(self):
        """Only recall.py references areas/ in functional code (not docs/comments)."""
        hits = self._scan_plugin_for("areas/")
        code_hits = [
            (f, l) for f, l in hits
            if f.endswith(".py")
            and not any(m in l for m in ("#", '"""', "'''"))
        ]
        # The only Python code-path hit should be recall.py
        non_recall = [(f, l) for f, l in code_hits if "recall.py" not in f]
        # frontmatter.py has _SLUG_PREFIXES = ("areas/", ...) — also a code hit
        # Filter for just live path construction (vault / "areas") not string constants
        path_construction = [
            (f, l) for f, l in non_recall
            if "vault" in l and "areas" in l
        ]
        assert not path_construction, (
            f"Unexpected code path building areas/ path (outside recall.py):\n" +
            "\n".join(f"  {f}: {l}" for f, l in path_construction)
        )

    def test_frontmatter_slug_prefixes_includes_areas_plural(self):
        """frontmatter.py _SLUG_PREFIXES has 'areas/' — this is a wikilink normalizer,
        not a vault-path builder: safe to singularize but not urgent."""
        fm_src = (SCRIPTS_DIR / "frontmatter.py").read_text()
        assert "areas/" in fm_src, (
            "frontmatter.py no longer has areas/ in slug prefixes"
        )

    def test_follow_up_due_and_known_files_are_all_follow_ups_plural_users(self):
        """Enumerate every Python file referencing follow-ups — classify each."""
        hits = self._scan_plugin_for("follow-ups")
        py_hits = [
            (f, l) for f, l in hits
            if f.endswith(".py")
            and "follow-ups" in l
        ]
        # Known files that reference follow-ups (as a dir path or key):
        # 1. follow_up_due.py — live, builds follow-ups/ path
        # 2. regenerate_indices.py — JOBS entry, builds follow-ups/ path
        # 3. migrate_radar_to_follow_ups.py — one-shot migration tool
        # 4. status_validator.py — CANONICAL key "follow-ups" (live, needs singularize)
        # 5. agent_ruleset.py — descriptive text only (comment/docstring)
        # 6. migrate_vault.py — "follow-ups"→"backlog" migration mapping
        # 7. vault.py — comment/docstring only
        known_files = {
            "follow_up_due.py", "regenerate_indices.py", "migrate_radar_to_follow_ups.py",
            "status_validator.py", "agent_ruleset.py", "migrate_vault.py", "vault.py",
        }
        files_with_hits = {Path(f).name for f, _ in py_hits}
        unexpected = files_with_hits - known_files
        assert not unexpected, (
            f"NEW Python files reference follow-ups/ (not in known set):\n" +
            "\n".join(f"  {f}" for f in sorted(unexpected))
        )
        # Confirm the live path-building files ARE present
        assert "follow_up_due.py" in files_with_hits, "follow_up_due.py missing from follow-ups hits"
        assert "regenerate_indices.py" in files_with_hits, "regenerate_indices.py missing from follow-ups hits"
        assert "status_validator.py" in files_with_hits, "status_validator.py missing — CANONICAL key may be gone"

    def test_starter_docs_reference_plural_dirs(self):
        """Starter docs (README, glossary) reference plural dirs — doc-only, not code."""
        starter_dir = PLUGIN_ROOT / "starter"
        readme = (starter_dir / "README.md").read_text()
        glossary = (starter_dir / "glossary.md").read_text()
        # These are doc-only references — not code paths
        assert "sessions/" in readme or "session/" in readme
        assert "areas/" in readme or "area/" in readme

    def test_no_new_writes_to_plural_sessions_dir_in_session_store(self):
        """session_store.py no longer writes to sessions/ plural dir."""
        ss_src = (SCRIPTS_DIR / "session_store.py").read_text()
        # Should NOT have code that does vault / "sessions" as a write destination
        # The Slice 1 migration moved writes to session/ (singular)
        # Check for any path construction to sessions/
        path_to_plural_sessions = re.findall(
            r'["\']sessions["\']|/ "sessions"| / \'sessions\'',
            ss_src
        )
        # Only docstring/comment references are expected (not live path construction)
        # Count non-comment occurrences
        non_comment_lines = [
            l for l in ss_src.splitlines()
            if ("sessions" in l and not l.lstrip().startswith("#")
                and '"""' not in l and "'''" not in l and "docstring" not in l.lower())
        ]
        # After Slice 1, session_store.py should NOT construct paths to sessions/
        live_path_constructions = [
            l for l in non_comment_lines
            if '"sessions"' in l or "'sessions'" in l
        ]
        assert not live_path_constructions, (
            f"session_store.py still constructs sessions/ path (expected retired after S1):\n" +
            "\n".join(f"  {l}" for l in live_path_constructions)
        )
