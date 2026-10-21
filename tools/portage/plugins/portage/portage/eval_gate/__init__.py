"""The eval gate — which prose-eval cases a branch touches, and how they run.

``mapping`` strictly loads a corpus's ``evals/eval-gate.json``; ``select`` turns a
git diff into the cases to run and writes the plan. ``portage eval-gate plan`` is
the CLI over ``select.plan``.
"""

from .mapping import Case, Mapping, MappingError, load_mapping
from .select import plan, unmapped_runner_cases

__all__ = ["Case", "Mapping", "MappingError", "load_mapping", "plan", "unmapped_runner_cases"]
