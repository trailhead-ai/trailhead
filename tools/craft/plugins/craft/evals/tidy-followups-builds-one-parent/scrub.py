#!/usr/bin/env python3
"""Deterministic scrub applied to everything this case writes into `runs/`.

    scrub.py <file> [<file> ...]      rewrite files in place (`.json` files per string value)

Placeholders, applied in this order:

- Real prefixes become `$WORK`, `$HOME`, `$USER` (when the writer knows them).
- The snapshot's craft and lore plugin roots, `$WORK/snap-<arm>/tools/craft/plugins/craft` and
  `.../tools/lore/plugins/lore`, become `$CRAFT[<arm>]` and `$LORE[<arm>]`; any other path in the
  snapshot becomes `$SNAP[<arm>]:<path>`. `$WORK/runs/<cell>-<arm>` becomes `$RUN[<cell>-<arm>]`.
  Every line still says which arm it came from, and what follows a placeholder is short enough,
  or a repo path `capture_scan.py` recognises structurally, so the scan does not flag it.
- A checkout of this repo under `$HOME/.local/state/camp/<group>/worktrees/<ws>/<member>/`
  becomes `$REPO:`.
- A SHA or revision hash (40 or more hex characters) becomes `[sha:<first 8>]`: stable across
  files, and two hashes still differ.
- Whatever `capture_scan.py` still flags as a credential class becomes `[redacted:<class>]`.
  The detection is the scanner's own, imported, not copied.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import capture_scan  # noqa: E402

_REPO_CHECKOUT = re.compile(r"\$HOME/\.local/state/camp/[^/\s]+/worktrees/[^/\s]+/[^/\s]+/")
_CRAFT_ROOT = re.compile(r"\$WORK/snap-([a-z]+)/tools/craft/plugins/craft\b")
_LORE_ROOT = re.compile(r"\$WORK/snap-([a-z]+)/tools/lore/plugins/lore\b")
_SNAPSHOT = re.compile(r"\$WORK/snap-([a-z]+)(?:/|\b)")
_RUN_DIR = re.compile(r"\$WORK/runs/([A-Za-z0-9._-]+)")
_HASH = re.compile(r"\b[A-Fa-f0-9]{40,}\b")
_MAX_PASSES = 5


def _redact(text: str) -> str:
    for _ in range(_MAX_PASSES):
        hits = {
            f.text: f.cls for f in capture_scan.scan_text(text) if capture_scan.is_credential_class(f.cls)
        }
        if not hits:
            break
        for token in sorted(hits, key=len, reverse=True):
            text = text.replace(token, f"[redacted:{hits[token]}]")
    return text


def scrub_text(
    text: str,
    work: str | None = None,
    home: str | None = None,
    user: str | None = None,
    repo: str | None = None,
) -> str:
    if repo:
        text = text.replace(repo.rstrip("/") + "/", "$REPO:")
    if work:
        text = text.replace(work.rstrip("/"), "$WORK")
    if home:
        text = text.replace(home.rstrip("/"), "$HOME")
    if user:
        text = re.sub(rf"(?<![\w$]){re.escape(user)}(?![\w])", "$USER", text)
    text = _REPO_CHECKOUT.sub("$REPO:", text)
    text = _CRAFT_ROOT.sub(r"$CRAFT[\1]", text)
    text = _LORE_ROOT.sub(r"$LORE[\1]", text)
    text = _SNAPSHOT.sub(lambda m: f"$SNAP[{m.group(1)}]" + (":" if m.group(0).endswith("/") else ""), text)
    text = _RUN_DIR.sub(r"$RUN[\1]", text)
    text = _HASH.sub(lambda m: f"[sha:{m.group(0)[:8].lower()}]", text)
    return _redact(text)


def scrub_value(value, **kw):
    if isinstance(value, str):
        return scrub_text(value, **kw)
    if isinstance(value, list):
        return [scrub_value(v, **kw) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v, **kw) for k, v in value.items()}
    return value


def scrub_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        path.write_text(json.dumps(scrub_value(json.loads(text)), indent=1) + ("\n" if text.endswith("\n") else ""))
    else:
        path.write_text(scrub_text(text))


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        scrub_file(Path(arg))
