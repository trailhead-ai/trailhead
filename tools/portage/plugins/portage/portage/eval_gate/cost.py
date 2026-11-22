"""`portage eval-gate cost`: what a one-file edit to each procedure file of a plugin costs at the gate.

A *procedure file* is a regular ``.md`` file under ``<plugin root>/skills/`` at HEAD (the plugin root
is the parent of each ``evals/eval-gate.json`` whose directory name is the plugin) that at least one case
selects. Files no case selects are listed apart and left out of every figure. For each procedure file the
cases a one-file edit touches come from one ``select_touched_by_path`` call for every procedure file,
with HEAD as the merge base (no git call of this module's own): HEAD's tree, the base listing and each
mapping are read once, so the git commands run grow with the mappings, not with the number of procedure
files times the mappings. The push tier is the touched cases not ``merge_only``, the merge
tier is every touched case. Both tiers price a case on its corpus ``runner.model``.

A case's price is the median per-run cost (``costUsd`` plus ``judgeCostUsd`` where present) of the
recorded runs of that (plugin, case, model). A file's total is the sum of its push-tier prices plus the
sum of its merge-tier prices, so a push case counts twice. The baseline is the median of the file
totals, printed as ``baseline: <n>``; while any selected case has no run it is
``baseline: incomplete (<n> cases unpriced); lower bound <median>`` (an unpriced case adds nothing).
The report opens with the baseline, the mean beside it and the unpriced count, and ends with the same
``baseline:`` line, so the last line of stdout is the figure.

Run sources, all read-only:

- the gate's history (both generations), read with ``timings.history_lines`` and ``timings.decision``.
  A history run needs a ``model`` that has the shape of a model id: lines with none, a null, or a
  mis-shaped one are model-unknown. Lines of a pinned runner are excluded.
- every ``*.json`` file in each ``--runs-from`` directory and in each of its immediate subdirectories,
  in sorted name order (so a request directory's ``<case>/batch-2.json`` and ``<case>/aggregate-result.json``
  are read without being named), read through ``eval_core.run.runs_of``, ``run_record`` and ``measure``.
  Each directory is opened ``O_NOFOLLOW | O_DIRECTORY`` and its files are opened relative to that
  descriptor; a symlinked subdirectory is not followed, no deeper level is read, and a directory whose
  device and inode had their own files read already (including one also passed as its own ``--runs-from``)
  has them read once; a directory passed as ``--runs-from`` always has its immediate subdirectories read, so
  the result does not depend on the order of the ``--runs-from`` directories.
  That check keys on ``(st_dev, st_ino)``, so on a filesystem that reports non-unique or zero inode numbers
  (some FUSE and network mounts) distinct directories can be taken for one and the files of all but the
  first are dropped with no tally.
  A runner output counts for the one plugin named by ``suite.plugins``, the case named by
  ``cases[0].name`` and the model named by ``suite.modelOverride``.

A run is counted unless it is excluded, for exactly one reason, checked in this order: ``ablation``
(the runner output's ``suite.ablation`` is not ``none``), ``multi-plugin`` (``suite.plugins`` is not a
single plugin), ``other-plugin`` (it is not ``--plugin``), ``pinned``, ``model-unknown``, ``zero-cost``
(no positive ``costUsd``), ``duplicate`` (same plugin, case, model, ``startedAt`` and cost as a run
already counted, so a run repeated across a case's top-level output, its batch files and its aggregate
result counts once; a repeat with no valid ``startedAt`` is not deduplicated), ``duplicate-history`` (a
history run matched to a runner-output run, below), ``run-cap`` (the retained-run
cap was already reached).

Every runner output is read before any history line. A counted history run is matched one-to-one against
the counted runner-output runs of the same (plugin, case, model) with exactly the same per-run cost
(``timings.run_cost``), through a per-key count of unconsumed runner-output runs kept in ``_Runs``; a
match is excluded as ``duplicate-history``, so a gate run present in both sources is priced once. Matching
consumes runner-output runs only (two identical history runs need a counterpart each), and an excluded
run neither consumes nor is consumed. A history run with no counterpart is counted. The rule accepts one
false merge: two distinct runs of one case on one model with bit-identical cost, one in each source,
count once.

A counted run is *matched* when a selected case maps to its (case, model).

Hostile input. A runner-output file is read with ``O_NOFOLLOW | O_NONBLOCK``, at most ``MAX_FILE_BYTES``,
and skipped (tallied by reason, never printed) when it is not a regular file or cannot be read
(``not-regular``), is over the size bound (``too-large``), is past the call's byte budget
(``directory-bound``), is not JSON (``not-json``), nests deeper than ``MAX_DEPTH`` or past the parser's own
limit (``too-deep``), has a shape the reader does not take or a case name outside the mapping's shape
(``bad-shape``), holds more runs than ``MAX_RUNS_PER_FILE`` (``too-many-runs``), or holds a
``costUsd`` or ``judgeCostUsd`` that is present and not finite, non-negative and
at most ``MAX_RUN_COST_USD`` (``bad-number``). A subdirectory that cannot be opened or listed is tallied
``unlistable-directory`` and the report goes on; an unlistable ``--runs-from`` itself raises ``CostError``,
as does one that is itself a symlink or not a directory (the CLI prints
``runs-from unreadable: <path> is not a directory``).

Three bounds cover the whole call, however many directories it reads: ``max_total_bytes`` (every byte read,
whether or not the file then parsed; a file's size is checked against what remains before it is read, so the
call can exceed the budget by at most one file, ``MAX_FILE_BYTES`` + 1),
``max_total_entries`` (every entry listed in any listing, whatever its
kind or name; each listing takes at most what remains, and one cut short is tallied ``listings-truncated``)
and ``max_retained_runs`` (the runs kept in memory; one past it is excluded as ``run-cap``). When any of
``directory-bound``, ``listings-truncated``, ``unlistable-directory``, ``run-cap``, ``too-large`` or
``too-many-runs`` is nonzero the figure is partial, and both ``baseline:`` lines end with
``(partial: <name> <n>, ...)`` naming those tallies. The malformed-file reasons (``not-json``, ``too-deep``,
``bad-shape``, ``bad-number``, ``not-regular``) stay out of the suffix: they are bad input, not a bound.
The bounds are the fixed module constants ``MAX_FILE_BYTES``, ``MAX_RUNS_PER_FILE``, ``MAX_TOTAL_BYTES``,
``MAX_TOTAL_ENTRIES`` and ``MAX_RETAINED_RUNS``, and the command has no flag for any of them: to get a full
figure, narrow ``--runs-from`` to the directories that matter.
History lines are tallied as ``history lines: read <n>; skipped: corrupt <n>, mis-shaped <n>`` (the words
and order of ``portage eval-gate timings``, classified by ``timings.classify_line`` so the two agree on one
file): ``corrupt`` is a line that is not a JSON object, ``mis-shaped`` any other unusable line (failing
``timings.decision``, a case or plugin name outside the mapping's shape). Either count nonzero also makes
the figure partial, named ``history-corrupt`` and ``history-mis-shaped`` after the bound tallies. Those
lines are skipped, never repaired, and no flag changes that: inspect the history file for the lines the
reader cannot use.
Only names that passed the mapping's shapes and numbers computed here reach stdout; paths go through
``verdict._safe``.

``report`` raises ``CostError`` naming the failed source (``repo``, ``mapping``, ``runs-from``, ``history``).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import stat
import statistics
from pathlib import Path, PurePosixPath

from portage.eval_core.run import MAX_RUN_COST_USD, measure, run_record, runs_of

from . import history, timings
from .mapping import CASE_NAME, MappingError
from .select import _BLOB_MODES, GitError, _head_tree, _is_mapping, select_touched_by_path
from .verdict import _safe

MAX_FILE_BYTES = 1 << 20
MAX_TOTAL_BYTES = 64 << 20
MAX_TOTAL_ENTRIES = 20000
MAX_RETAINED_RUNS = 100000
MAX_DEPTH = 10
MAX_RUNS_PER_FILE = 1000
THIN_RUNS = 2
SKIP_REASONS = ("not-regular", "too-large", "directory-bound", "not-json", "too-deep", "bad-shape",
                "bad-number", "unlistable-directory", "too-many-runs")
RUN_EXCLUSIONS = ("ablation", "multi-plugin", "other-plugin", "pinned", "model-unknown", "zero-cost",
                  "duplicate", "duplicate-history", "run-cap")
_STARTED = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?Z")


class CostError(Exception):
    def __init__(self, source: str, detail: str):
        super().__init__(f"{source} unreadable: {detail}")
        self.source = source


class _Runs:
    """The counted runs by (case, model), with the tallies of what was left out and what is left of the
    whole ``report`` call's byte, listing-entry and retained-run allowances."""

    def __init__(self, bytes_left: int, entries_left: int, runs_left: int) -> None:
        self.bytes_left = bytes_left
        self.entries_left = entries_left
        self.runs_left = runs_left
        self.costs: dict[tuple[str, str], list[float]] = {}
        self.seen: set[tuple] = set()
        self.unmatched: dict[tuple[str, str, str, float], int] = {}
        self.history_read = 0
        self.history_corrupt = 0
        self.history_mis_shaped = 0
        self.excluded = dict.fromkeys(RUN_EXCLUSIONS, 0)
        self.files_read = 0
        self.files_skipped = dict.fromkeys(SKIP_REASONS, 0)
        self.listings_truncated = 0
        self.directories_read: dict[tuple[int, int], list[str]] = {}
        self.directories_expanded: set[tuple[int, int]] = set()

    def add(self, plugin: str, case: str, model: str | None, started: str | None, cost_usd: float | None,
            total: float, reason: str | None, from_history: bool = False) -> None:
        if reason is None and model is None:
            reason = "model-unknown"
        if reason is None and not (cost_usd is not None and cost_usd > 0):
            reason = "zero-cost"
        key = (plugin, case, model, started, total)
        if reason is None and started is not None and key in self.seen:
            reason = "duplicate"
        match = (case, model, total, plugin)
        if reason is None and from_history and self.unmatched.get(match, 0) > 0:
            self.unmatched[match] -= 1
            reason = "duplicate-history"
        if reason is None and self.runs_left <= 0:
            reason = "run-cap"
        if reason is not None:
            self.excluded[reason] += 1
            return
        if started is not None:
            self.seen.add(key)
        self.runs_left -= 1
        if not from_history:
            self.unmatched[match] = self.unmatched.get(match, 0) + 1
        self.costs.setdefault((case, model), []).append(total)


def _depth(doc: object) -> int:
    deepest, stack = 0, [(doc, 1)]
    while stack:
        node, level = stack.pop()
        deepest = max(deepest, level)
        if deepest > MAX_DEPTH:
            break
        if isinstance(node, dict):
            stack.extend((v, level + 1) for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            stack.extend((v, level + 1) for v in node if isinstance(v, (dict, list)))
    return deepest


def _plugins_of(suite: dict) -> list[str] | None:
    plugins = suite.get("plugins")
    if not isinstance(plugins, list) or len(plugins) > 16:
        return None
    names = [p.get("name") if isinstance(p, dict) else p for p in plugins]
    return names if all(isinstance(n, str) and CASE_NAME.match(n) for n in names) else None


def _slurp(f, limit: int) -> bytes:
    return f.read(limit)


def _read_file(name: str, dir_fd: int, budget: int) -> tuple[str | None, object, int]:
    """``(skip reason or None, parsed JSON, bytes read)``."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except OSError:
        return "not-regular", None, 0
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        return "not-regular", None, 0
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return "not-regular", None, 0
    try:
        with os.fdopen(fd, "rb") as f:
            if st.st_size > MAX_FILE_BYTES:
                return "too-large", None, 0
            if st.st_size > budget:
                return "directory-bound", None, 0
            data = _slurp(f, MAX_FILE_BYTES + 1)
    except OSError:
        return "not-regular", None, 0
    if len(data) > MAX_FILE_BYTES:
        return "too-large", None, len(data)
    try:
        return None, json.loads(data), len(data)
    except RecursionError:
        return "too-deep", None, len(data)
    except ValueError:
        return "not-json", None, len(data)


def _runner_output(doc: object):
    """``(plugins, ablation, model, case, runs)`` of a runner output, or the skip reason."""
    if _depth(doc) > MAX_DEPTH:
        return "too-deep"
    suite = doc.get("suite") if isinstance(doc, dict) else None
    plugins = _plugins_of(suite) if isinstance(suite, dict) else None
    runs = runs_of(doc)
    case = doc["cases"][0].get("name") if runs is not None else None
    if (plugins is None or runs is None or not (isinstance(case, str) and CASE_NAME.match(case))):
        return "bad-shape"
    if len(runs) > MAX_RUNS_PER_FILE:
        return "too-many-runs"
    for run in runs:
        for key in ("costUsd", "judgeCostUsd"):
            if run.get(key) is not None and measure(run[key], MAX_RUN_COST_USD) is None:
                return "bad-number"
    return plugins, suite.get("ablation"), history.runner_model(doc), case, runs


def _open_directory(name: str | Path, dir_fd: int | None = None) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def _ingest_files(dir_fd: int, plugin: str, runs: _Runs) -> list[str]:
    """Read the ``.json`` files of an open directory the first time it is reached, and list it once; the
    names of its subdirectories, sorted, as listed then."""
    st = os.fstat(dir_fd)
    key = (st.st_dev, st.st_ino)
    if key in runs.directories_read:
        return runs.directories_read[key]
    runs.directories_read[key] = []
    left = runs.entries_left
    with os.scandir(dir_fd) as it:
        listed = [(e.name, e.is_dir(follow_symlinks=False)) for e in itertools.islice(it, left + 1)]
    if len(listed) > left:
        runs.listings_truncated += 1
    listed = listed[:left]
    runs.entries_left -= len(listed)
    for name in sorted(n for n, _ in listed if n.endswith(".json")):
        skip, doc, nbytes = _read_file(name, dir_fd, runs.bytes_left)
        runs.bytes_left -= nbytes
        parsed = skip or _runner_output(doc)
        if isinstance(parsed, str):
            runs.files_skipped[parsed] += 1
            continue
        runs.files_read += 1
        plugins, ablation, model, case, records = parsed
        reason = ("ablation" if ablation != "none" else "multi-plugin" if len(plugins) != 1
                  else "other-plugin" if plugins[0] != plugin else None)
        for raw in records:
            rec = run_record(raw)
            started = raw.get("startedAt")
            started = started if isinstance(started, str) and _STARTED.fullmatch(started) else None
            runs.add(plugin, case, model, started, rec["cost_usd"], timings.run_cost(rec), reason)
    subdirectories = sorted(n for n, is_dir in listed if is_dir)
    runs.directories_read[key] = subdirectories
    return subdirectories


def _first_visit(fd: int, seen: set[tuple[int, int]]) -> bool:
    st = os.fstat(fd)
    key = (st.st_dev, st.st_ino)
    if key in seen:
        return False
    seen.add(key)
    return True


def _ingest_directory(directory: Path, plugin: str, runs: _Runs) -> None:
    """Read ``directory``'s ``.json`` files, then those of each immediate subdirectory."""
    try:
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            raise CostError("runs-from", f"{_safe(directory)} is not a directory")
        top = _open_directory(directory)
    except OSError as e:
        raise CostError("runs-from", f"{_safe(directory)}: {e.strerror}") from None
    try:
        if not _first_visit(top, runs.directories_expanded):
            return
        try:
            subdirectories = _ingest_files(top, plugin, runs)
        except OSError as e:
            raise CostError("runs-from", f"{_safe(directory)}: {e.strerror}") from None
        for name in subdirectories:
            try:
                sub = _open_directory(name, top)
            except OSError:
                runs.files_skipped["unlistable-directory"] += 1
                continue
            try:
                _ingest_files(sub, plugin, runs)
            except OSError:
                runs.files_skipped["unlistable-directory"] += 1
            finally:
                os.close(sub)
    finally:
        os.close(top)


def _ingest_history(env: dict[str, str], plugin: str, runs: _Runs) -> None:
    try:
        for path in (history.rotated_path(env), history.history_path(env)):
            for raw in timings.history_lines(path):
                runs.history_read += 1
                shape, doc, dec = timings.classify_line(raw)
                if dec is None:
                    if shape == "corrupt":
                        runs.history_corrupt += 1
                    else:
                        runs.history_mis_shaped += 1
                    continue
                case = doc["case"]
                reason = ("other-plugin" if doc["plugin"] != plugin
                          else "pinned" if doc["pinned_runner"] else None)
                for r in dec["runs"]:
                    runs.add(plugin, case, dec["model"], None, r["cost_usd"], r["cost"], reason,
                             from_history=True)
    except timings.TimingsError as e:
        raise CostError("history", str(e)) from None


def _procedure_files(tree: dict[str, str], plugin: str) -> dict[str, list[str]]:
    roots = [PurePosixPath(p).parent.parent for p in tree if _is_mapping(p)]
    out: dict[str, list[str]] = {}
    for root in sorted(r for r in roots if r.name == plugin):
        prefix = (root / "skills").as_posix() + "/"
        out[root.as_posix()] = sorted(p for p, mode in tree.items()
                                      if mode in _BLOB_MODES and p.startswith(prefix) and p.endswith(".md"))
    return out


def _money(x: float) -> str:
    return f"{x:.4f}"


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in rows]


def report(repo: Path, plugin: str, runs_from: list[Path], env: dict[str, str],
           *, max_total_bytes: int = MAX_TOTAL_BYTES, max_total_entries: int = MAX_TOTAL_ENTRIES,
           max_retained_runs: int = MAX_RETAINED_RUNS) -> list[str]:
    """The report's lines for ``plugin``'s procedure files in ``repo`` against the recorded runs."""
    repo = Path(repo)
    if not repo.is_dir():
        raise CostError("repo", f"{_safe(repo)} is not a directory")
    try:
        tree = _head_tree(repo)
        by_root = _procedure_files(tree, plugin)
        every = [p for paths in by_root.values() for p in paths]
        touched = select_touched_by_path(repo, "HEAD", every, merge_only_either=True, tree=tree)
        selected: dict[str, tuple[str, list[dict]]] = {
            path: (root, [c for c in touched[path] if c["plugin_root"] == root])
            for root, paths in by_root.items() for path in paths}
    except GitError as e:
        raise CostError("repo", _safe(e)) from None
    except MappingError as e:
        raise CostError("mapping", _safe(e)) from None
    except OSError as e:
        raise CostError("repo", e.strerror or type(e).__name__) from None
    if not by_root:
        raise CostError("mapping", f"no eval-gate.json mapping for plugin {_safe(plugin)}")

    runs = _Runs(max_total_bytes, max_total_entries, max_retained_runs)
    for directory in runs_from:
        _ingest_directory(Path(directory), plugin, runs)
    _ingest_history(env, plugin, runs)

    def price(entry: dict) -> tuple[float, int] | None:
        costs = runs.costs.get((entry["case"], entry["runner"]["model"]))
        return (statistics.median(costs), len(costs)) if costs else None

    rows, excluded, totals = [], [], []
    cases: dict[tuple[str, str], dict] = {}
    for path, (root, entries) in selected.items():
        if not entries:
            excluded.append(path)
            continue
        push = [e for e in entries if not e["merge_only"]]
        cost_of = lambda es: sum(p[0] for p in map(price, es) if p)  # noqa: E731
        total = cost_of(push) + cost_of(entries)
        totals.append(total)
        gaps = sum(1 for e in entries if price(e) is None)
        rows.append([_safe(path), str(len(push)), str(len(entries)), _money(cost_of(push)),
                     _money(cost_of(entries)), _money(total), str(gaps)])
        for e in entries:
            cases[(root, e["case"])] = e
    unpriced = sorted((k[1], "merge" if e["merge_only"] else "push+merge")
                      for k, e in cases.items() if price(e) is None)
    median = statistics.median(totals) if totals else None
    if median is None:
        closing = "baseline: none (no procedure file is selected by a case)"
    elif unpriced:
        closing = f"baseline: incomplete ({len(unpriced)} cases unpriced); lower bound {_money(median)}"
    else:
        closing = f"baseline: {_money(median)}"
    cuts = [("directory-bound", runs.files_skipped["directory-bound"]),
            ("listings-truncated", runs.listings_truncated),
            ("unlistable-directory", runs.files_skipped["unlistable-directory"]),
            ("run-cap", runs.excluded["run-cap"]),
            ("too-large", runs.files_skipped["too-large"]),
            ("too-many-runs", runs.files_skipped["too-many-runs"]),
            ("history-corrupt", runs.history_corrupt),
            ("history-mis-shaped", runs.history_mis_shaped)]
    partial = ", ".join(f"{name} {n}" for name, n in cuts if n)
    if partial:
        closing += f" (partial: {partial})"
    mean = f"mean: {_money(statistics.mean(totals))} across {len(totals)} files" if totals \
        else "mean: none across 0 files"
    wanted = {(e["case"], e["runner"]["model"]) for e in cases.values()}
    counted = sum(len(v) for v in runs.costs.values())
    matched = sum(len(v) for k, v in runs.costs.items() if k in wanted)
    out = [closing, mean, f"unpriced: {len(unpriced)} cases",
           f"runner outputs: read {runs.files_read}; skipped: "
           + ", ".join(f"{r} {runs.files_skipped[r]}" for r in SKIP_REASONS)
           + f"; listings-truncated {runs.listings_truncated}",
           f"history lines: read {runs.history_read}; skipped: corrupt {runs.history_corrupt}, "
           f"mis-shaped {runs.history_mis_shaped}",
           f"runs: counted {counted} (matched {matched}); excluded: "
           + ", ".join(f"{r} {runs.excluded[r]}" for r in RUN_EXCLUSIONS),
           "procedure files:"]
    out += _table([["path", "push-cases", "merge-cases", "push-cost", "merge-cost", "total", "unpriced"],
                   *sorted(rows)]) if rows else ["  none"]
    out.append("unpriced cases:")
    out += _table([[c, t] for c, t in unpriced]) if unpriced else ["  none"]
    out.append("priced cases:")
    priced = sorted((k[1], "merge" if e["merge_only"] else "push+merge", *price(e))
                    for k, e in cases.items() if price(e))
    out += _table([[c, t, _money(p), str(n), "thin" if n <= THIN_RUNS else "-"]
                   for c, t, p, n in priced]) if priced else ["  none"]
    out.append("files no case selects:")
    out += [f"  {_safe(p)}" for p in sorted(excluded)] or ["  none"]
    out.append(closing)
    return out
