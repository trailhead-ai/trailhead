"""`portage eval-gate timings`: what the timing history says about each mapped case.

Reads the timing history (``history.py``, both generations) and the ``evals/eval-gate.json``
mappings under a repository, and reports per case how long its decisions took. Writes nothing.

A history line is *counted* only when its ``plugin`` and ``case`` have the mapping's case-name
shape and name a mapped case, its ``fingerprint`` equals that case's current pass-cache key for the
line's own tier (either tier's when the line records none), computed from the repository the way the gate
computes it, and ``pinned_runner`` is not true. Once a corpus sets ``push_model`` one row mixes push-model
and merge-model runs, so its median, batch-max and mean cost blend both models.
Every other line is tallied as ``corrupt`` (not a JSON object), ``mis-shaped`` (a field outside
its shape or bounds), ``pinned`` or ``stale`` (an old fingerprint, or a case the mappings do not
hold) and nothing from it is printed. Only validated names and numbers computed here reach stdout.

A decision is *single-batch* when it holds at least one run and no more runs than the gate's first batch,
``min(pass_at, runs - pass_at + 1)`` for the case's mapped ``runs`` and ``pass_at`` (the
fingerprint covers both, so a counted line was made under the mapping read here). Its
``elapsed_seconds`` is then one batch's elapsed time; a multi-batch decision's is not, and is left
out of the single-batch figures. The budget left is ``MAX_TIMEOUT_SECONDS - canary.TIMEOUT_SECONDS``.

Flags: ``timed-out`` (a decision ended ``timeout``, or a scored run has no duration: the case
counts as at least the full budget, ``MAX_TIMEOUT_SECONDS``, and also takes the next two flags);
``cannot-finish-a-split`` (a single-batch decision took longer than half the budget left, the gate's
first-batch-elapsed against budget-left test in ``run.py``); ``over-merge-margin`` (a merge-only case whose largest
single-batch elapsed exceeds the budget left).
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import statistics
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from portage.eval_core import run as core

from . import history
from .canary import TIMEOUT_SECONDS as CANARY_SECONDS
from .loads import LoadError, cached, derive, tree_reader
from .mapping import CASE_NAME, MappingError, _q, load_mapping, valid_corpus_path
from .run import MAX_TIMEOUT_SECONDS, _cache_key
from .select import MAPPING_NAME, TIERS, resolve_runner
from .verdict import _safe

DEFAULT_DECLARED_TIMEOUT = 300
_STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
MAX_HISTORY_BYTES = 2 * history.MAX_BYTES
_VERDICTS = {"pass", "fail", "not-run"}
_SKIP_DIRS = {".git", "node_modules"}


class TimingsError(Exception):
    pass


@dataclass
class _Case:
    plugin: str
    name: str
    merge_only: bool
    first_batch: int
    keys: dict[str, str | None]
    declared: str
    decisions: list[dict] = field(default_factory=list)


def _budget_left() -> int:
    return MAX_TIMEOUT_SECONDS - CANARY_SECONDS


def _declared_timeout(case_dir: Path) -> str:
    """The case's ``timeout_seconds`` (``prompt.md`` frontmatter, else ``case.yaml``
    ``execution:``, else 300), or ``?`` when a file is unreadable or declares it in a shape not read."""
    try:
        prompt = (case_dir / "prompt.md").read_text() if (case_dir / "prompt.md").is_file() else ""
        front = re.match(r"---\n(.*?)\n---\n", prompt, re.S)
        for text, strict in ((front.group(1) if front else "", r"timeout_seconds:\s*([0-9]{1,6})\s*"),
                             ((case_dir / "case.yaml").read_text(), r"\s+timeout_seconds:\s*([0-9]{1,6})\s*")):
            found = None
            for line in text.splitlines():
                if "timeout_seconds" not in line:
                    continue
                m = re.fullmatch(strict, line)
                if not m:
                    return "?"
                found = found if found is not None else int(m.group(1))
            if found is not None:
                return str(found)
    except (OSError, UnicodeDecodeError):
        return "?"
    return str(DEFAULT_DECLARED_TIMEOUT)


def _mapped_cases(repo: Path, only_plugin: str | None) -> dict[tuple[str, str], _Case]:
    if not repo.is_dir():
        raise TimingsError(f"--repo {repo} is not a directory")
    cases: dict[tuple[str, str], _Case] = {}
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        base = Path(dirpath)
        if base.name != "evals" or MAPPING_NAME not in filenames:
            continue
        plugin_dir = base.parent
        rel = plugin_dir.relative_to(repo).as_posix()
        root = "" if rel == "." else rel
        if not valid_corpus_path(root):
            raise TimingsError("a corpus path has characters outside A-Za-z0-9._/- or a '..' segment")
        mapping_file = base / MAPPING_NAME
        try:
            mapping = load_mapping(mapping_file.read_text(), f"{root}/evals/{MAPPING_NAME}".lstrip("/"))
        except (MappingError, OSError, UnicodeDecodeError) as e:
            raise TimingsError(f"mapping unreadable: {e}") from None
        plugin = PurePosixPath(root).name
        if only_plugin is not None and plugin != only_plugin:
            continue
        read = cached(tree_reader(plugin_dir))
        for position, (name, case) in enumerate(mapping.cases.items(), 1):
            try:
                derived = derive(case.entry, case.branches, read).files if case.entry else ()
            except LoadError as e:
                raise TimingsError(f"mapping unreadable: case {_q(name, position)}: {e}") from None
            measures = list(dict.fromkeys((*case.measures, *derived)))
            keys = {tier: _cache_key(plugin_dir, {
                "case": name, "runner": resolve_runner(mapping.runner, tier, case.needs_merge_model),
                "runs": case.runs, "pass_at": case.pass_at, "plugin_root": root,
                "measures": measures}) for tier in TIERS}
            cases[(plugin, name)] = _Case(
                plugin, name, case.merge_only, core._first_batch(case.runs, case.pass_at),
                keys, _declared_timeout(base / name))
    return cases


def _number(v: object, bound: float) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    try:
        return math.isfinite(v) and 0 <= v <= bound
    except OverflowError:
        return False


def run_cost(record: dict) -> float:
    """A run record's cost: its ``cost_usd`` plus its ``judge_cost_usd``, each where present."""
    return sum(record[k] for k in ("cost_usd", "judge_cost_usd") if record.get(k) is not None)


def decision(doc: dict) -> dict | None:
    """The validated numbers of a history line, or ``None`` when a field is outside its shape."""
    runs = doc.get("runs")
    if not (isinstance(doc.get("plugin"), str) and isinstance(doc.get("case"), str)
            and isinstance(doc.get("recorded_at"), str) and _STAMP.fullmatch(doc["recorded_at"])
            and (doc.get("fingerprint") is None or isinstance(doc["fingerprint"], str))
            and isinstance(doc.get("pinned_runner"), bool) and doc.get("verdict") in _VERDICTS
            and _number(doc.get("elapsed_seconds"), 10 * core.MAX_DURATION_SECONDS)
            and isinstance(runs, list)):
        return None
    out = []
    for r in runs:
        if not (isinstance(r, dict) and isinstance(r.get("errored"), bool)
                and (r.get("duration_seconds") is None or _number(r["duration_seconds"], core.MAX_DURATION_SECONDS))
                and all(r.get(k) is None or _number(r[k], core.MAX_RUN_COST_USD)
                        for k in ("cost_usd", "judge_cost_usd"))):
            return None
        out.append({"errored": r["errored"], "duration": r.get("duration_seconds"), "cost": run_cost(r),
                    "cost_usd": r.get("cost_usd")})
    return {"at": doc["recorded_at"], "elapsed": doc["elapsed_seconds"], "runs": out,
            "timeout": doc.get("reason") == "timeout", "version": doc.get("runner_version"),
            "model": history.model_id(doc.get("model"))}


def history_lines(path: Path) -> list[bytes]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise TimingsError(f"history {path.name} unreadable: {e.strerror}") from None
    try:
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise TimingsError(f"history {path.name} is not a regular file")
            if st.st_size > MAX_HISTORY_BYTES:
                raise TimingsError(f"history {path.name} is too large ({st.st_size} bytes, "
                                   f"over {MAX_HISTORY_BYTES})")
            data = f.read(MAX_HISTORY_BYTES + 1)
            if len(data) > MAX_HISTORY_BYTES:
                raise TimingsError(f"history {path.name} is too large (over {MAX_HISTORY_BYTES} bytes)")
            return [x for x in data.split(b"\n") if x.strip()]
    except OSError as e:
        raise TimingsError(f"history {path.name} unreadable: {e.strerror}") from None


def classify_line(raw: bytes) -> tuple[str, dict | None, dict | None]:
    """``("corrupt" | "mis-shaped" | "ok", doc, decision)`` of one history line: ``corrupt`` is a line that
    is not a JSON object, ``mis-shaped`` an object whose fields, ``plugin`` or ``case`` are outside their shapes."""
    try:
        doc = json.loads(raw)
    except Exception:
        doc = None
    if not isinstance(doc, dict):
        return "corrupt", None, None
    dec = decision(doc)
    plugin, name = doc.get("plugin"), doc.get("case")
    if dec is None or not CASE_NAME.match(name) or not (plugin == "" or CASE_NAME.match(plugin)):
        return "mis-shaped", doc, None
    return "ok", doc, dec


def _line_keys(case: _Case, doc: dict) -> set[str]:
    """The keys a line may carry: its own tier's when it records one, else either tier's."""
    tier = doc.get("tier")
    tiers = (tier,) if isinstance(tier, str) and tier in case.keys else TIERS
    return {case.keys[t] for t in tiers if case.keys[t] is not None}


def _ingest(env: dict[str, str], cases: dict[tuple[str, str], _Case], only_plugin: str | None):
    tally = {"corrupt": 0, "mis-shaped": 0, "stale": 0, "pinned": 0}
    stamps: list[str] = []
    versions: set[str] = set()
    total = 0
    for path in (history.rotated_path(env), history.history_path(env)):
        for raw in history_lines(path):
            total += 1
            shape, doc, dec = classify_line(raw)
            if dec is None:
                tally[shape] += 1
                continue
            plugin, name = doc["plugin"], doc["case"]
            if only_plugin is not None and plugin != only_plugin:
                continue
            case = cases.get((plugin, name))
            if doc["pinned_runner"]:
                tally["pinned"] += 1
            elif case is None or doc["fingerprint"] is None or doc["fingerprint"] not in _line_keys(case, doc):
                tally["stale"] += 1
            else:
                case.decisions.append(dec)
                stamps.append(dec["at"])
                version = history.runner_version({"claudeVersion": dec["version"]})
                if version:
                    versions.add(version)
    return tally, stamps, versions, total


def _stats(case: _Case) -> dict:
    left = _budget_left()
    scored = [r["duration"] for d in case.decisions for r in d["runs"]
              if not r["errored"] and r["duration"] is not None]
    null_runs = sum(1 for d in case.decisions for r in d["runs"] if not r["errored"] and r["duration"] is None)
    timeouts = sum(1 for d in case.decisions if d["timeout"])
    single = [d["elapsed"] for d in case.decisions if 0 < len(d["runs"]) <= case.first_batch]
    timed_out = bool(timeouts or null_runs)
    batch_max = max([MAX_TIMEOUT_SECONDS] * timed_out + single) if (timed_out or single) else None
    flags = []
    if timed_out:
        flags.append("timed-out")
    if timed_out or any(e > left / 2 for e in single):
        flags.append("cannot-finish-a-split")
    if timed_out or (case.merge_only and batch_max is not None and batch_max > left):
        flags.append("over-merge-margin")
    cost = sum(r["cost"] for d in case.decisions for r in d["runs"]) / len(case.decisions)
    return {"decisions": len(case.decisions), "runs": sum(len(d["runs"]) for d in case.decisions),
            "timeouts": timeouts, "null-runs": null_runs,
            "median": _fmt(statistics.median(scored)) if scored else "-",
            "max": _fmt(max(scored)) if scored else "-",
            "batch-max": ("-" if batch_max is None else ">=" * timed_out + _fmt(batch_max)),
            "mean-cost": f"{cost:.4f}", "flags": ",".join(flags) or "-"}


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def report(repo: Path, only_plugin: str | None, env: dict[str, str]) -> list[str]:
    """The report's lines for ``repo``'s mappings against the history ``env`` names."""
    cases = _mapped_cases(Path(repo), only_plugin)
    tally, stamps, versions, total = _ingest(env, cases, only_plugin)
    where = _safe(history.history_path(env))
    if total == 0:
        return [f"history: {where}: no runs recorded yet"]
    measured = sorted((c for c in cases.values() if c.decisions), key=lambda c: (c.plugin, c.name))
    rows = [(c, _stats(c)) for c in measured]
    rows.sort(key=lambda r: r[1]["flags"] == "-")
    span = f"{min(stamps)} .. {max(stamps)}" if stamps else "none"
    out = [f"history: {where} (+ {history.ROTATED_NAME}); counted lines dated {span}; "
           f"runner versions: {', '.join(sorted(versions)) or 'none'}"]
    cols = ["plugin", "case", "tier", "timeout", "decisions", "runs", "timeouts", "null-runs",
            "median", "max", "batch-max", "mean-cost", "flags"]
    table = [cols]
    for c, s in rows:
        table.append([c.plugin or "-", c.name, "merge-only" if c.merge_only else "core", c.declared,
                      *(str(s[k]) for k in cols[4:])])
    widths = [max(len(r[i]) for r in table) for i in range(len(cols))]
    out += ["  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in table]
    names = lambda cs: ", ".join(f"{c.plugin or '-'}/{c.name}" for c in cs) or "none"  # noqa: E731
    thin = [c for c in measured if len(c.decisions) < 2]
    unmeasured = sorted((c for c in cases.values() if not c.decisions), key=lambda c: (c.plugin, c.name))
    out.append(f"fewer than two decisions: {names(thin)}; no counted lines: {names(unmeasured)}; "
               f"skipped lines: corrupt {tally['corrupt']}, mis-shaped {tally['mis-shaped']}, "
               f"stale {tally['stale']}, pinned {tally['pinned']}")
    return out
