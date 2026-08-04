"""Evaluation (PRD §11).

The app is the source of truth. The harness reads from the app's stores and logs to the
recording surface; a prompt living in both would drift within a week (§11.1).
"""

from .gold import Gold, available, load
from .harness import EvalReport, run
from .scoring import Score, StageReport

__all__ = ["EvalReport", "Gold", "Score", "StageReport", "available", "load", "run"]
