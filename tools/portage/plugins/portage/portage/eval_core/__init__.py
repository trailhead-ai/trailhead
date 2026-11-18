"""The eval core: request-driven runs of one eval case through a harness's eval runner.

``run.decide_case`` runs and decides one case; ``run.run_canary`` runs the sandbox-write
canary. The eval gate (``portage.eval_gate.run``) adds its gate-only checks around them.
"""

from .run import (
    TmpBaseRefused,
    TmpPathTooLong,
    decide_case,
    execute,
    run_canary,
)

__all__ = ["TmpBaseRefused", "TmpPathTooLong", "decide_case", "execute", "run_canary"]
