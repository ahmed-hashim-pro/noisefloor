import pytest

from noisefloor.diff import TargetChanged, diff_runs
from noisefloor.run import CaseRun, RunRecord
from noisefloor.scoring import ScoreResult
from noisefloor.target import Invocation


def inv(repeat: int, *, outcome: str = "ok") -> Invocation:
    return Invocation(
        case_id="a",
        repeat=repeat,
        argv=["x"],
        stdout="{}",
        stderr="",
        exit_code=0 if outcome == "ok" else 1,
        duration_s=1.0,
        outcome=outcome,
    )


def result(passed: bool) -> ScoreResult:
    return ScoreResult(
        key="0:contains",
        type="contains",
        kind="binary",
        direction="higher_is_better",
        value=1.0 if passed else 0.0,
        passed=passed,
    )


def case(case_id: str, passes: int, n: int, *, ok: bool = True, dh: str = "h1"):
    invocations = [inv(i, outcome="ok" if ok else "error:exit") for i in range(n)]
    scores = [[result(i < passes)] if ok else [] for i in range(n)]
    return CaseRun(
        case_id=case_id, definition_hash=dh, invocations=invocations, scores=scores
    )


def record(*cases: CaseRun, run_id: str = "r", command=None) -> RunRecord:
    repeats = len(cases[0].invocations) if cases else 5
    return RunRecord(
        run_id=run_id,
        suite_name="demo",
        suite_hash="s1",
        target_command=command or ["cmd", "{{input}}"],
        target_cwd=".",
        git_sha=None,
        repeats=repeats,
        jobs=1,
        started_at="2026-09-02T10:00:00+00:00",
        finished_at="2026-09-02T10:01:00+00:00",
        harness_version="0.1.0",
        cases=list(cases),
    )


def verdict(before: CaseRun, after: CaseRun) -> str:
    d = diff_runs(record(before, run_id="b"), record(after, run_id="c"))
    return d.cases[0].verdict


# -- verdicts --------------------------------------------------------------


def test_identical_runs_show_no_regression() -> None:
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(case("a", 5, 5)))
    assert d.cases[0].verdict == "unchanged"
    assert d.exit_code == 0


def test_degradation_is_flagged() -> None:
    assert verdict(case("a", 5, 5), case("a", 2, 5)) == "regressed"


def test_wobble_inside_the_band_is_not_flagged() -> None:
    assert verdict(case("a", 3, 5), case("a", 2, 5)) == "unchanged"


def test_broke_outranks_regressed() -> None:
    d = diff_runs(
        record(case("a", 5, 5), case("b", 5, 5), run_id="b"),
        record(case("a", 0, 5), case("b", 0, 5, ok=False)),
    )
    assert {c.case_id: c.verdict for c in d.cases} == {
        "a": "regressed",
        "b": "broke",
    }
    assert d.exit_code == 2


def test_fixed_is_reported_but_does_not_fail_the_build() -> None:
    d = diff_runs(
        record(case("a", 0, 5, ok=False), run_id="b"), record(case("a", 5, 5))
    )
    assert d.cases[0].verdict == "fixed"
    assert d.exit_code == 0


def test_errors_on_both_sides_are_not_a_regression() -> None:
    d = diff_runs(
        record(case("a", 0, 5, ok=False), run_id="b"), record(case("a", 0, 5, ok=False))
    )
    assert d.cases[0].verdict == "unchanged"
    assert d.exit_code == 0


def test_added_and_removed_cases_are_not_regressions() -> None:
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(case("b", 0, 5)))
    assert {c.case_id: c.verdict for c in d.cases} == {
        "a": "removed",
        "b": "added",
    }
    assert d.exit_code == 0


def test_a_redefined_case_is_excluded_from_the_verdict() -> None:
    d = diff_runs(
        record(case("a", 5, 5, dh="h1"), run_id="b"),
        record(case("a", 0, 5, dh="h2")),
    )
    assert d.cases[0].verdict == "redefined"
    assert d.exit_code == 0
    assert any("redefined" in w for w in d.warnings)


def test_a_scorer_missing_on_one_side_is_unmeasured() -> None:
    """The target succeeded but produced no scores, so there is nothing to compare."""
    empty = CaseRun(
        case_id="a", definition_hash="h1", invocations=[inv(0), inv(1)], scores=[[], []]
    )
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(empty))
    assert d.cases[0].verdict == "unmeasured"
    assert d.exit_code == 0


# -- guards ----------------------------------------------------------------


def test_a_changed_target_command_refuses_by_default() -> None:
    with pytest.raises(TargetChanged):
        diff_runs(
            record(case("a", 5, 5), run_id="b", command=["old"]),
            record(case("a", 5, 5), command=["new"]),
        )


def test_a_changed_target_can_be_allowed_with_a_warning() -> None:
    d = diff_runs(
        record(case("a", 5, 5), run_id="b", command=["old"]),
        record(case("a", 5, 5), command=["new"]),
        allow_target_change=True,
    )
    assert any("target command" in w for w in d.warnings)


def test_mismatched_repeat_counts_are_warned_about() -> None:
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(case("a", 3, 3)))
    assert any("repeat" in w.lower() for w in d.warnings)


def test_cases_are_ordered_worst_first() -> None:
    d = diff_runs(
        record(case("a", 5, 5), case("b", 5, 5), case("c", 5, 5), run_id="b"),
        record(case("a", 5, 5), case("b", 0, 5, ok=False), case("c", 0, 5)),
    )
    assert [c.verdict for c in d.cases] == ["broke", "regressed", "unchanged"]
