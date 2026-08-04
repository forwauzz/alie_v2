"""Shadow mode (PRD §9.1, §9.3).

Run a candidate over the same input as the incumbent, diff the results, report the
disagreements. That is how you *choose* an engine instead of guessing.

Nothing here is promoted automatically. A shadow run produces a comparison; the decision is
a human's, and the flag register is where it gets recorded.

Two disciplines from §9.3, enforced rather than documented:

**One variable at a time.** Ten flags is 1,024 configurations and the golds cannot be run
against all of them. `compare_flag` takes a baseline and *one* change; a caller trying to
diff a grab-bag config gets an error naming every flag that moved.

**Some features change output without changing scores.** Dedupe on does not improve
extraction accuracy — it removes duplicate rows, which may *lower* row recall against a
gold that contains them. That is correct behaviour, not a regression. So a comparison
reports the metric delta *and* how much output moved, and refuses to call either one a
verdict.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import flags as flag_registry
from .gold import Gold
from .harness import EvalReport, run


class NotOneVariable(ValueError):
    """More than one flag moved between baseline and candidate (§9.3).

    Refusing is the point: a grab-bag config that scores better tells you nothing about
    which change earned it, and combinations explode faster than golds can be run.
    """


@dataclass(frozen=True)
class MetricDelta:
    name: str
    baseline: float
    candidate: float

    @property
    def delta(self) -> float:
        return self.candidate - self.baseline

    @property
    def moved(self) -> bool:
        return abs(self.delta) > 1e-9

    def __str__(self) -> str:
        arrow = "+" if self.delta > 0 else ""
        return f"{self.name}: {self.baseline:.0%} -> {self.candidate:.0%} ({arrow}{self.delta:.1%})"


@dataclass
class Shadow:
    """One candidate measured against one incumbent, over the same input."""

    gold_id: str
    variable: str
    baseline_value: Any
    candidate_value: Any
    deltas: list[MetricDelta] = field(default_factory=list)
    #: Rows whose text differs between the two runs. Output movement is not a score, and
    #: reporting it separately is what keeps dedupe from looking like a regression (§9.3).
    rows_changed: int = 0
    rows_total: int = 0
    #: Must-holds are absolute. A candidate that breaks one is not a candidate, whatever
    #: its metrics did (§11.3).
    baseline_holds: bool = True
    candidate_holds: bool = True

    @property
    def improved(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.delta > 0]

    @property
    def regressed(self) -> list[MetricDelta]:
        return [d for d in self.deltas if d.delta < 0]

    @property
    def safe(self) -> bool:
        """Whether the candidate may be *considered*. Never whether it should be adopted —
        that is a human decision, and this module does not make it."""
        return self.candidate_holds

    def summary(self) -> str:
        head = (
            f"{self.gold_id}: {self.variable} "
            f"{self.baseline_value!r} -> {self.candidate_value!r}"
        )
        lines = [head, "-" * len(head)]
        moved = [d for d in self.deltas if d.moved]
        lines.extend(f"  {d}" for d in moved) if moved else lines.append("  no metric moved")
        lines.append(
            f"  output: {self.rows_changed}/{self.rows_total} rows differ"
            " (movement is not a score - see §9.3)"
        )
        if not self.candidate_holds:
            lines.append("  CANDIDATE BREAKS A MUST-HOLD - not adoptable")
        return "\n".join(lines)


def compare_flag(
    conn: sqlite3.Connection,
    gold: Gold,
    *,
    flag: str,
    candidate: Any,
    baseline: dict[str, Any] | None = None,
) -> Shadow:
    """Score `gold` twice — once on the baseline, once with exactly one flag changed.

    The baseline defaults to the register's own defaults, which is what "incumbent" means
    when nobody has said otherwise. One variable holds by construction here; `compare`
    is the path where it has to be checked.
    """
    base_flags = dict(baseline or {})
    return compare(conn, gold, baseline=base_flags, candidate=base_flags | {flag: candidate})


def compare(
    conn: sqlite3.Connection,
    gold: Gold,
    *,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> Shadow:
    """Score `gold` under two arbitrary flag sets, refusing anything but a single change.

    This is the path a caller can misuse, so this is where the discipline is enforced —
    a guard on `compare_flag`, which builds the candidate from the baseline, could never
    fire and would be reassurance rather than a check.
    """
    resolved_base = flag_registry.resolve(run_flags=baseline)
    resolved_cand = flag_registry.resolve(run_flags=candidate)
    variable = _require_one_variable(resolved_base, resolved_cand)

    before = run(conn, gold, flags=baseline)
    after = run(conn, gold, flags=candidate)
    return _diff(
        gold, variable, resolved_base.get(variable), resolved_cand.get(variable), before, after
    )


def _require_one_variable(base: dict[str, Any], candidate: dict[str, Any]) -> str:
    """Return the single flag that moved, or refuse."""
    moved = sorted(k for k in set(base) | set(candidate) if base.get(k) != candidate.get(k))
    if len(moved) > 1:
        raise NotOneVariable(
            "measure one variable at a time against a fixed baseline (§9.3); "
            f"these moved together: {', '.join(moved)}"
        )
    if not moved:
        raise NotOneVariable("baseline and candidate resolve to the same flag set")
    return moved[0]


def _diff(
    gold: Gold, flag: str, base_value: Any, cand_value: Any,
    before: EvalReport, after: EvalReport,
) -> Shadow:
    shared = sorted(set(before.scores) & set(after.scores))
    deltas = [MetricDelta(name, before.scores[name], after.scores[name]) for name in shared]

    # Row-level movement, keyed by date+title so a re-ordering is not counted as a change.
    def keyed(report: EvalReport) -> dict[tuple, tuple]:
        return {
            (r["date"], r["title"]): tuple(r["bullets"]) for r in report.rows
        }

    a, b = keyed(before), keyed(after)
    changed = sum(1 for k in set(a) | set(b) if a.get(k) != b.get(k))

    return Shadow(
        gold_id=gold.id,
        variable=flag,
        baseline_value=base_value,
        candidate_value=cand_value,
        deltas=deltas,
        rows_changed=changed,
        rows_total=max(len(a), len(b)),
        baseline_holds=before.holds,
        candidate_holds=after.holds,
    )
