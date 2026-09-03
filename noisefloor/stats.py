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

#: Both significance clauses compare a float delta against a configured
#: constant (``min_rate_drop`` or ``min_effect``), and an exact-threshold delta
#: can land a few ULPs to either side depending on which arithmetic produced
#: it — 4/5 - 3/5 evaluates to 0.20000000000000007 while 3/5 - 2/5 evaluates to
#: 0.19999999999999996; 1.1 - 1.0 evaluates to 0.10000000000000009 while
#: 0.5 - 0.4 evaluates to 0.09999999999999998. Pass rates are quantized at
#: 1/n, so once this much error is absorbed the comparison is exact for any
#: realistic n; continuous effects are not quantized at all, so they are, if
#: anything, more exposed. An absolute tolerance is the right shape for both:
#: these scorers produce magnitudes in a similar range (0-1 confidence/rate
#: scores, small numbers of seconds), so one constant suffices without scaling.
_THRESHOLD_EPSILON = 1e-9


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

    @property
    def rate_band(self) -> str:
        """Pass count out of N, regardless of scorer kind.

        A continuous scorer also has a pass rate (from its optional bounds),
        and the binary clauses in ``_worse`` must report *that* number, not
        the mean/range from :attr:`band` — printing the value band there would
        show two nearly-identical numbers next to a "rate dropped" verdict.
        """
        return f"{self.pass_count}/{self.n}"


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
) -> str | None:
    """Reason `after`'s pass rate is worse than `before`'s, or None.

    Covers only the two binary clauses (spec 6.3: unanimous-baseline and
    rate-drop), which apply to every scorer kind via its pass rate and are
    genuinely symmetric -- a delta past a threshold is the same test read
    from either side. The continuous mean/range clause is deliberately not
    here; see `_continuous_move` for why that one can't be evaluated by
    calling this same helper with arguments swapped.
    """
    if before.pass_rate == 1.0 and after.pass_rate < 1.0:
        return (
            f"unanimous baseline {before.rate_band} → {after.rate_band}; "
            "a clean baseline showed no variance, so any failure is new"
        )
    if before.pass_rate - after.pass_rate > min_rate_drop + _THRESHOLD_EPSILON:
        return (
            f"pass rate {before.rate_band} → {after.rate_band} "
            f"(drop > {min_rate_drop:.2f})"
        )
    return None


def _continuous_move(
    baseline: ScorerAggregate,
    candidate: ScorerAggregate,
    *,
    min_effect: float,
) -> Significance | None:
    """The continuous range-plus-effect clause (spec 6.3), or None.

    This is *not* implemented as a call to `_worse` with arguments swapped,
    unlike the two binary clauses above -- do not "simplify" it back into
    one. The baseline is the side deliberately measured as a noise
    reference before anything changed; its observed band is the only one
    either direction may test against. A zero-width baseline band is not
    "no information" -- it is the *strongest* evidence available that a
    difference is real, since zero observed noise means a difference can't
    be noise. Using the candidate's own band as the reference (which a
    swapped call produced for the improvement direction) was the actual
    defect: the candidate's spread describes nothing about the baseline's
    noise, so it must never stand in for it.
    """
    higher_better = baseline.direction == "higher_is_better"
    effect = abs(baseline.mean - candidate.mean)
    if effect <= min_effect + _THRESHOLD_EPSILON:
        return None

    reason = (
        f"mean {baseline.mean:.3f} (n={baseline.n}) → {candidate.mean:.3f} "
        f"(n={candidate.n}), outside the baseline band "
        f"[{baseline.vmin:.3f}–{baseline.vmax:.3f}]"
    )
    worse = (
        candidate.mean < baseline.vmin
        if higher_better
        else candidate.mean > baseline.vmax
    )
    if worse:
        return Significance(baseline.key, "regressed", reason)
    better = (
        candidate.mean > baseline.vmax
        if higher_better
        else candidate.mean < baseline.vmin
    )
    if better:
        return Significance(baseline.key, "improved", reason)
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

    if (reason := _worse(baseline, candidate, min_rate_drop=min_rate_drop)) is not None:
        return Significance(key, "regressed", reason)
    if (reason := _worse(candidate, baseline, min_rate_drop=min_rate_drop)) is not None:
        return Significance(key, "improved", reason)

    # Baseline is the fixed reference in both directions here -- see
    # _continuous_move's docstring for why this one clause isn't a mirrored
    # _worse call like the two above it.
    if baseline.kind == "continuous" and (
        sig := _continuous_move(baseline, candidate, min_effect=min_effect)
    ):
        return sig

    return Significance(
        key, "unchanged", f"{baseline.band} → {candidate.band}, within noise"
    )
