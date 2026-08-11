"""AST-based regression guard: every read_text() / write_text() call across
the whole camp source tree must pin encoding="utf-8" explicitly, rather than
relying on the platform's preferred locale encoding (which fails under
LC_ALL=C).

Test contract:
- Walk every .py file under plugins/camp/camp/ and AST-parse it.
- For every Call node whose attribute is read_text or write_text, assert an
  `encoding=` keyword is present.
- Widened form of test_scaffold_stub_write_uses_utf8_encoding (single-file,
  cli/group.py only) to cover the full tree mechanically.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CAMP_SRC = Path(__file__).resolve().parents[1] / "plugins" / "camp" / "camp"


def _iter_source_files() -> list[Path]:
    return sorted(_CAMP_SRC.rglob("*.py"))


def test_read_write_text_calls_pin_utf8_encoding() -> None:
    offenders: list[str] = []
    for src_path in _iter_source_files():
        tree = ast.parse(src_path.read_text(encoding="utf-8"), filename=str(src_path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("read_text", "write_text")
            ):
                kwarg_names = {kw.arg for kw in node.keywords}
                if "encoding" not in kwarg_names:
                    offenders.append(f"{src_path}:{node.lineno}")

    assert not offenders, (
        "read_text()/write_text() call(s) missing encoding=\"utf-8\":\n"
        + "\n".join(offenders)
    )
