import pytest

from noisefloor.diff import TargetChanged, _ok_rate, diff_runs
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


def record(
    *cases: CaseRun,
    run_id: str = "r",
    command=None,
    env: dict[str, str] | None = None,
) -> RunRecord:
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
        env=env or {},
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
        case_id="a",
        definition_hash="h1",
        invocations=[inv(i) for i in range(5)],
        scores=[[] for _ in range(5)],
    )
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(empty))
    assert d.cases[0].verdict == "unmeasured"
    assert d.exit_code == 0


def test_no_comparable_scorers_on_either_side_is_unmeasured() -> None:
    """Zero scorers on both sides is stronger evidence of nothing-measured than
    one-sided emptiness, so it must resolve to unmeasured too, not unchanged."""
    empty_before = CaseRun(
        case_id="a",
        definition_hash="h1",
        invocations=[inv(i) for i in range(5)],
        scores=[[] for _ in range(5)],
    )
    empty_after = CaseRun(
        case_id="a",
        definition_hash="h1",
        invocations=[inv(i) for i in range(5)],
        scores=[[] for _ in range(5)],
    )
    d = diff_runs(record(empty_before, run_id="b"), record(empty_after))
    assert d.cases[0].verdict == "unmeasured"
    assert d.exit_code == 0


def test_an_improvement_with_no_regression_is_flagged_as_improved() -> None:
    assert verdict(case("a", 2, 5), case("a", 5, 5)) == "improved"


def mixed(case_id: str, oks: list[bool], *, dh: str = "h1") -> CaseRun:
    """`oks[i]` says whether repeat i succeeded, so a case can be genuinely
    `degraded` (some but not all repeats erroring), not just fully ok or fully
    errored like `case()` above produces."""
    invocations = [
        inv(i, outcome="ok" if ok else "error:exit") for i, ok in enumerate(oks)
    ]
    scores = [[result(True)] if ok else [] for ok in oks]
    return CaseRun(
        case_id=case_id, definition_hash=dh, invocations=invocations, scores=scores
    )


def test_fewer_repeats_at_the_same_success_rate_is_not_degradation() -> None:
    """Issue #6: 5/5 and 2/2 are both a 100% success rate. A candidate that
    simply ran fewer repeats than the baseline must not be read as
    degradation just because its raw ok_count is smaller."""
    d = diff_runs(
        record(case("a", 5, 5), run_id="b"),
        record(case("a", 2, 2)),
    )
    assert not any("degraded" in w for w in d.warnings)
    assert "degraded" not in d.cases[0].note


def test_a_genuine_rate_drop_still_warns_even_with_more_candidate_repeats() -> None:
    """The fix must not become "raw counts never trigger the warning" -- a
    real drop in success rate has to keep firing even when the candidate ran
    *more* repeats than the baseline, which a naive "fewer repeats" special
    case could miss."""
    d = diff_runs(
        record(case("a", 2, 2), run_id="b"),
        record(mixed("a", [True, True, True, False, False]), run_id="c"),
    )
    assert any("degraded" in w for w in d.warnings)
    assert "3/5" in d.cases[0].note and "2/2" in d.cases[0].note


def test_ok_rate_of_zero_invocations_is_zero_not_a_zero_division_error() -> None:
    """A case with no invocations on one side (denominator zero) must not
    raise; treated as a 0% rate rather than undefined."""
    assert _ok_rate(0, 0) == 0.0


def test_partial_degradation_is_noted_and_warned_about() -> None:
    """Two of five candidate repeats erroring must be visible even though the
    case does not `broke` and the exit code does not change for it alone."""
    d = diff_runs(
        record(case("a", 5, 5), run_id="b"),
        record(mixed("a", [True, True, True, False, False]), run_id="c"),
    )
    assert d.cases[0].verdict != "broke"
    assert "3/5" in d.cases[0].note and "5/5" in d.cases[0].note
    assert any("degraded" in w for w in d.warnings)
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


def test_an_env_difference_is_warned_about_not_refused() -> None:
    """A byte-identical target command with a changed env (e.g. a model swap
    via `MODEL=sonnet` -> `MODEL=opus`) must not diff silently against a
    stale baseline -- but must also not be refused the way a command change
    is, since env is how CLI tests intentionally drive behaviour changes."""
    d = diff_runs(
        record(case("a", 5, 5), run_id="b", env={"MODEL": "sonnet"}),
        record(case("a", 5, 5), env={"MODEL": "opus"}),
    )
    assert any("env" in w.lower() for w in d.warnings)
    assert d.exit_code == 0


def test_mismatched_repeat_counts_are_warned_about() -> None:
    d = diff_runs(record(case("a", 5, 5), run_id="b"), record(case("a", 3, 3)))
    assert any("repeat" in w.lower() for w in d.warnings)


def test_a_per_case_repeat_override_is_warned_about_alone() -> None:
    """RunRecord.repeats is the suite-level default; two runs can share it
    (here both 5, taken from case "a") while a single case's `repeats:`
    override differs between baseline and candidate. That must still surface
    per case -- a suite-level comparison alone would miss it entirely."""
    d = diff_runs(
        record(case("a", 5, 5), case("b", 3, 3), run_id="b"),
        record(case("a", 5, 5), case("b", 5, 5)),
    )
    b = next(c for c in d.cases if c.case_id == "b")
    assert "repeat count changed: 3 in the baseline vs 5 in the candidate" in b.note
    assert any("'b'" in w and "repeat count changed" in w for w in d.warnings)


def test_repeat_count_change_and_ok_count_degradation_are_distinguishable() -> None:
    """A case can both change its repeat count and lose ok repeats between
    baseline and candidate -- an intentional change to how many times it runs,
    versus partial degradation of the target under test. These are different
    conditions and neither may hide the other."""
    d = diff_runs(
        record(case("a", 5, 5), case("b", 5, 5), run_id="b"),
        record(case("a", 5, 5), mixed("b", [True, True, False])),
    )
    b = next(c for c in d.cases if c.case_id == "b")
    assert "repeat count changed: 5 in the baseline vs 3 in the candidate" in b.note
    assert "2/3 repeats ok in the candidate, down from 5/5 in the baseline" in b.note

    repeat_warnings = [w for w in d.warnings if "repeat count changed" in w]
    degraded_warnings = [w for w in d.warnings if "degraded" in w]
    assert len(repeat_warnings) == 1 and "'b'" in repeat_warnings[0]
    assert len(degraded_warnings) == 1 and "'b'" in degraded_warnings[0]


def test_a_broken_case_still_reports_a_repeat_count_change() -> None:
    """`broke` and a repeat-count change are independent facts about a case
    and can co-occur; the note must carry both, not just whichever the early
    `broke` return happens to produce."""
    d = diff_runs(
        record(case("a", 5, 5), run_id="b"), record(case("a", 0, 3, ok=False))
    )
    c = d.cases[0]
    assert c.verdict == "broke"
    assert "every repeat failed" in c.note
    assert "repeat count changed: 5 in the baseline vs 3 in the candidate" in c.note


def test_cases_are_ordered_worst_first() -> None:
    d = diff_runs(
        record(case("a", 5, 5), case("b", 5, 5), case("c", 5, 5), run_id="b"),
        record(case("a", 5, 5), case("b", 0, 5, ok=False), case("c", 0, 5)),
    )
    assert [c.verdict for c in d.cases] == ["broke", "regressed", "unchanged"]
