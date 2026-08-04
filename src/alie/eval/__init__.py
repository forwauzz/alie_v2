"""Evaluation (PRD §11).

The app is the source of truth. The harness reads from the app's stores and logs to the
recording surface; a prompt living in both would drift within a week (§11.1).
"""

from .gold import Gold, available, load
from .harness import EvalReport, run
from .scoring import Score, StageReport
from .shadow import NotOneVariable, Shadow, compare, compare_flag

__all__ = [
    "EvalReport",
    "Gold",
    "NotOneVariable",
    "Score",
    "Shadow",
    "StageReport",
    "available",
    "compare",
    "compare_flag",
    "load",
    "run",
]
