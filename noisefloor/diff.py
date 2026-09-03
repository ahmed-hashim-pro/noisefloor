"""Compare a candidate run against a baseline, one case at a time.

Cases are matched by id. A case whose definition changed is reported and then
excluded: a suite may evolve without invalidating the whole baseline, but a case
may not silently change meaning underneath its own id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from noisefloor.config import DEFAULT_MIN_EFFECT, DEFAULT_MIN_RATE_DROP
from noisefloor.run import CaseRun, RunRecord
from noisefloor.stats import (
    _THRESHOLD_EPSILON,
    ScorerAggregate,
    Significance,
    aggregate_case,
    compare,
)

CaseVerdict = Literal[
    "regressed",
    "improved",
    "unchanged",
    "broke",
    "fixed",
    "added",
    "removed",
    "redefined",
    "unmeasured",
]

#: Worst first, so the thing that fails the build is the thing you read first.
_ORDER = {
    "broke": 0,
    "regressed": 1,
    "redefined": 2,
    "unmeasured": 3,
    "removed": 4,
    "added": 5,
    "fixed": 6,
    "improved": 7,
    "unchanged": 8,
}


class TargetChanged(RuntimeError):
    """The two runs invoked different commands."""


@dataclass(frozen=True)
class CaseDiff:
    case_id: str
    verdict: CaseVerdict
    scorers: list[Significance] = field(default_factory=list)
    baseline: dict[str, ScorerAggregate] = field(default_factory=dict)
    candidate: dict[str, ScorerAggregate] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Diff:
    suite_name: str
    baseline_run_id: str
    candidate_run_id: str
    cases: list[CaseDiff]
    warnings: list[str] = field(default_factory=list)

    def by_verdict(self, verdict: CaseVerdict) -> list[CaseDiff]:
        return [c for c in self.cases if c.verdict == verdict]

    @property
    def exit_code(self) -> int:
        if self.by_verdict("broke"):
            return 2
        if self.by_verdict("regressed"):
            return 1
        return 0


def diff_runs(
    baseline: RunRecord,
    candidate: RunRecord,
    *,
    allow_target_change: bool = False,
    min_rate_drop: float = DEFAULT_MIN_RATE_DROP,
    min_effect: float = DEFAULT_MIN_EFFECT,
) -> Diff:
    warnings: list[str] = []

    if baseline.target_command != candidate.target_command:
        message = (
            f"target command changed: {baseline.target_command} → "
            f"{candidate.target_command}"
        )
        if not allow_target_change:
            raise TargetChanged(
                message + ". Re-baseline, or pass --allow-target-change."
            )
        warnings.append(message + " (comparing anyway, as requested)")

    if baseline.env != candidate.env:
        warnings.append(f"target env changed: {_env_diff(baseline.env, candidate.env)}")

    before, after = baseline.case_by_id, candidate.case_by_id
    cases: list[CaseDiff] = []

    for case_id in sorted(set(before) | set(after)):
        if case_id not in after:
            cases.append(CaseDiff(case_id, "removed", note="absent from the candidate"))
            continue
        if case_id not in before:
            cases.append(CaseDiff(case_id, "added", note="absent from the baseline"))
            continue
        cases.append(
            _compare_case(
                case_id,
                before[case_id],
                after[case_id],
                min_rate_drop=min_rate_drop,
                min_effect=min_effect,
                warnings=warnings,
            )
        )

    cases.sort(key=lambda c: (_ORDER[c.verdict], c.case_id))
    return Diff(
        suite_name=candidate.suite_name,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        cases=cases,
        warnings=warnings,
    )


def _compare_case(
    case_id: str,
    before: CaseRun,
    after: CaseRun,
    *,
    min_rate_drop: float,
    min_effect: float,
    warnings: list[str],
) -> CaseDiff:
    if before.definition_hash != after.definition_hash:
        warnings.append(f"case {case_id!r} was redefined since the baseline; excluded")
        return CaseDiff(case_id, "redefined", note="case definition changed")

    # A per-case `repeats:` override can change between baseline and candidate
    # even though the suite default (RunRecord.repeats) does not, so this is
    # checked per case rather than once for the whole run. It is reported
    # regardless of outcome, since it is a fact about how the case ran, not
    # about whether the target succeeded -- see the ok-rate note below for
    # that, a different condition that can fire independently or alongside it.
    repeats_note = ""
    if len(before.invocations) != len(after.invocations):
        repeats_note = (
            f"repeat count changed: {len(before.invocations)} in the baseline "
            f"vs {len(after.invocations)} in the candidate"
        )
        warnings.append(f"case {case_id!r} {repeats_note}")

    if before.ok_count > 0 and after.ok_count == 0:
        return CaseDiff(
            case_id, "broke", note=_join_notes(_error_note(after), repeats_note)
        )
    if before.ok_count == 0 and after.ok_count > 0:
        return CaseDiff(
            case_id, "fixed", note=_join_notes("the target now succeeds", repeats_note)
        )
    if before.ok_count == 0 and after.ok_count == 0:
        return CaseDiff(
            case_id,
            "unchanged",
            note=_join_notes("the target errored on both sides", repeats_note),
        )

    base_agg = aggregate_case(before.scores)
    cand_agg = aggregate_case(after.scores)

    significances: list[Significance] = []
    for key in sorted(set(base_agg) | set(cand_agg)):
        if key not in base_agg or key not in cand_agg:
            significances.append(
                Significance(key, "unmeasured", "scored on only one side")
            )
            continue
        significances.append(
            compare(
                base_agg[key],
                cand_agg[key],
                min_rate_drop=min_rate_drop,
                min_effect=min_effect,
            )
        )

    verdicts = {s.verdict for s in significances}
    if "regressed" in verdicts:
        resolved: CaseVerdict = "regressed"
    elif "improved" in verdicts:
        resolved = "improved"
    elif verdicts <= {"unmeasured", "skipped"}:
        resolved = "unmeasured"
    else:
        resolved = "unchanged"

    # Same two-clause shape as stats._worse, on the ok rate rather than the
    # scorer pass rate -- a wobble the significance rule calls noise (e.g.
    # 3/5 -> 2/5) must not be independently called degradation here just
    # because it's a different "ok" axis. (ok_count is > 0 on both sides by
    # this point, so _ok_rate's zero-denominator guard can't fire here.)
    degraded_note = ""
    before_rate = _ok_rate(before.ok_count, len(before.invocations))
    after_rate = _ok_rate(after.ok_count, len(after.invocations))
    genuine_drop = (before_rate == 1.0 and after_rate < 1.0) or (
        before_rate - after_rate > min_rate_drop + _THRESHOLD_EPSILON
    )
    if genuine_drop:
        degraded_note = (
            f"{after.ok_count}/{len(after.invocations)} repeats ok in the "
            f"candidate, down from {before.ok_count}/{len(before.invocations)} "
            "in the baseline"
        )
        warnings.append(f"case {case_id!r} degraded: {degraded_note}")

    return CaseDiff(
        case_id=case_id,
        verdict=resolved,
        scorers=significances,
        baseline=base_agg,
        candidate=cand_agg,
        note=_join_notes(degraded_note, repeats_note),
    )


def _ok_rate(ok_count: int, total: int) -> float:
    """0.0 for zero invocations, rather than raising -- nothing to compare
    should fail toward being noticed, not toward crashing."""
    return ok_count / total if total else 0.0


def _join_notes(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _error_note(case: CaseRun) -> str:
    outcomes = sorted({inv.outcome for inv in case.invocations})
    return f"every repeat failed: {', '.join(outcomes)}"


def _env_diff(before: dict[str, str], after: dict[str, str]) -> str:
    changed = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b != a:
            changed.append(f"{key}={b!r} -> {a!r}")
    return ", ".join(changed)
