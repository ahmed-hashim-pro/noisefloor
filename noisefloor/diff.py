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
from noisefloor.stats import ScorerAggregate, Significance, aggregate_case, compare

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

    if baseline.repeats != candidate.repeats:
        warnings.append(
            f"repeat counts differ ({baseline.repeats} vs {candidate.repeats}); "
            "the comparison is wider than it looks"
        )

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

    if before.ok_count > 0 and after.ok_count == 0:
        return CaseDiff(case_id, "broke", note=_error_note(after))
    if before.ok_count == 0 and after.ok_count > 0:
        return CaseDiff(case_id, "fixed", note="the target now succeeds")
    if before.ok_count == 0 and after.ok_count == 0:
        return CaseDiff(case_id, "unchanged", note="the target errored on both sides")

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
    elif verdicts and verdicts <= {"unmeasured", "skipped"}:
        resolved = "unmeasured"
    else:
        resolved = "unchanged"

    return CaseDiff(
        case_id=case_id,
        verdict=resolved,
        scorers=significances,
        baseline=base_agg,
        candidate=cand_agg,
    )


def _error_note(case: CaseRun) -> str:
    outcomes = sorted({inv.outcome for inv in case.invocations})
    return f"every repeat failed: {', '.join(outcomes)}"
