"""Aggregation and the two significance rules.

A single "delta exceeds the observed range" rule breaks on binary scorers, where
the range degenerates in both directions: a unanimous 5/5 baseline has range 0,
so any failure looks significant, while a 3/5 baseline has range 1, so a
collapse to 0/5 reads as noise. Hence two rules, chosen by scorer kind.

The observed range over a handful of samples is not a confidence interval. This
is a heuristic tuned to keep CI false positives low, and every report prints the
underlying counts so a human can disagree with it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Literal

from noisefloor.config import DEFAULT_MIN_EFFECT, DEFAULT_MIN_RATE_DROP
from noisefloor.scoring import Direction, ScoreResult, ScorerKind

Verdict = Literal["regressed", "improved", "unchanged", "unmeasured", "skipped"]

#: Pass rates are ratios of small integers, so a drop that is mathematically
#: equal to the threshold can land a few ULPs above it — 4/5 - 3/5 evaluates to
#: 0.20000000000000007 while 3/5 - 2/5 evaluates to 0.19999999999999996. Without
#: this tolerance two drops of identical magnitude get opposite verdicts.
_RATE_EPSILON = 1e-9


@dataclass(frozen=True)
class ScorerAggregate:
    key: str
    type: str
    kind: ScorerKind
    direction: Direction
    n: int
    pass_count: int
    values: tuple[float, ...]
    reliable: bool = True

    @property
    def pass_rate(self) -> float:
        return self.pass_count / self.n if self.n else 0.0

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    @property
    def vmin(self) -> float:
        return min(self.values) if self.values else 0.0

    @property
    def vmax(self) -> float:
        return max(self.values) if self.values else 0.0

    @property
    def band(self) -> str:
        if self.kind == "binary":
            return f"{self.pass_count}/{self.n}"
        return f"{self.mean:.3f} [{self.vmin:.3f}–{self.vmax:.3f}] n={self.n}"


def aggregate_case(
    per_repeat: list[list[ScoreResult]],
) -> dict[str, ScorerAggregate]:
    """Collapse per-repeat scores into one aggregate per scorer key.

    Repeats that errored contribute no results at all, so ``n`` is the number of
    *scored* repeats, not the number attempted.
    """
    collected: dict[str, list[ScoreResult]] = {}
    for results in per_repeat:
        for result in results:
            collected.setdefault(result.key, []).append(result)

    aggregates: dict[str, ScorerAggregate] = {}
    for key, results in collected.items():
        head = results[0]
        aggregates[key] = ScorerAggregate(
            key=key,
            type=head.type,
            kind=head.kind,
            direction=head.direction,
            n=len(results),
            pass_count=sum(1 for r in results if r.passed),
            values=tuple(r.value for r in results),
            reliable=all(r.reliable for r in results),
        )
    return aggregates


@dataclass(frozen=True)
class Significance:
    key: str
    verdict: Verdict
    reason: str


def _worse(
    before: ScorerAggregate,
    after: ScorerAggregate,
    *,
    min_rate_drop: float,
    min_effect: float,
) -> str | None:
    """Reason `after` is worse than `before`, or None."""
    if before.pass_rate == 1.0 and after.pass_rate < 1.0:
        return (
            f"unanimous baseline {before.band} → {after.band}; "
            "a clean baseline showed no variance, so any failure is new"
        )
    if before.pass_rate - after.pass_rate > min_rate_drop + _RATE_EPSILON:
        return f"pass rate {before.band} → {after.band} (drop > {min_rate_drop:.2f})"

    if before.kind == "continuous":
        higher_better = before.direction == "higher_is_better"
        outside = (
            after.mean < before.vmin if higher_better else after.mean > before.vmax
        )
        effect = abs(before.mean - after.mean)
        if outside and effect > min_effect:
            return (
                f"mean {before.mean:.3f} → {after.mean:.3f}, outside the baseline "
                f"band [{before.vmin:.3f}–{before.vmax:.3f}]"
            )
    return None


def compare(
    baseline: ScorerAggregate,
    candidate: ScorerAggregate,
    *,
    min_rate_drop: float = DEFAULT_MIN_RATE_DROP,
    min_effect: float = DEFAULT_MIN_EFFECT,
) -> Significance:
    key = baseline.key
    if not baseline.reliable or not candidate.reliable:
        return Significance(
            key, "skipped", "measured under concurrency; not comparable"
        )
    if baseline.n < 2 or candidate.n < 2:
        return Significance(
            key,
            "unmeasured",
            f"only {min(baseline.n, candidate.n)} scored repeat(s); noise unmeasured",
        )

    limits = {"min_rate_drop": min_rate_drop, "min_effect": min_effect}
    if (reason := _worse(baseline, candidate, **limits)) is not None:
        return Significance(key, "regressed", reason)
    if (reason := _worse(candidate, baseline, **limits)) is not None:
        return Significance(key, "improved", reason)
    return Significance(
        key, "unchanged", f"{baseline.band} → {candidate.band}, within noise"
    )
