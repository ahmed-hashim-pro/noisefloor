from dataclasses import replace

import pytest

from noisefloor.scoring import ScoreResult
from noisefloor.stats import aggregate_case, compare


def binary(passed: bool, key: str = "0:contains") -> ScoreResult:
    return ScoreResult(
        key=key,
        type="contains",
        kind="binary",
        direction="higher_is_better",
        value=1.0 if passed else 0.0,
        passed=passed,
    )


def continuous(value: float, *, direction="higher_is_better", passed=True, key="0:n"):
    return ScoreResult(
        key=key,
        type="json_path_number",
        kind="continuous",
        direction=direction,
        value=value,
        passed=passed,
    )


def agg_binary(passes: int, n: int):
    return aggregate_case([[binary(i < passes)] for i in range(n)])["0:contains"]


def agg_continuous(values, **kw):
    return aggregate_case([[continuous(v, **kw)] for v in values])["0:n"]


# -- aggregation -----------------------------------------------------------


def test_aggregate_counts_passes_and_collects_values() -> None:
    a = agg_binary(3, 5)
    assert (a.n, a.pass_count) == (5, 3)
    assert a.pass_rate == pytest.approx(0.6)


def test_aggregate_reports_the_observed_range() -> None:
    a = agg_continuous([0.4, 0.6, 0.5])
    assert (a.vmin, a.vmax) == (0.4, 0.6)
    assert a.mean == pytest.approx(0.5)


def test_a_scorer_is_unreliable_if_any_repeat_was() -> None:
    results = [[continuous(1.0)], [replace(continuous(1.0), reliable=False)]]
    assert not aggregate_case(results)["0:n"].reliable


# -- binary rule -----------------------------------------------------------


def test_unanimous_baseline_flags_a_single_failure() -> None:
    """A clean baseline observed no variance, so any failure is new behaviour."""
    assert compare(agg_binary(5, 5), agg_binary(4, 5)).verdict == "regressed"


def test_flaky_baseline_tolerates_one_more_failure() -> None:
    assert compare(agg_binary(3, 5), agg_binary(2, 5)).verdict == "unchanged"


def test_flaky_baseline_flags_two_more_failures() -> None:
    assert compare(agg_binary(3, 5), agg_binary(1, 5)).verdict == "regressed"


def test_total_collapse_from_a_flaky_baseline_is_flagged() -> None:
    """The degenerate case a raw observed-range rule would have missed."""
    assert compare(agg_binary(3, 5), agg_binary(0, 5)).verdict == "regressed"


def test_identical_binary_results_are_unchanged() -> None:
    assert compare(agg_binary(3, 5), agg_binary(3, 5)).verdict == "unchanged"


def test_improvement_is_the_mirror_of_regression() -> None:
    assert compare(agg_binary(4, 5), agg_binary(5, 5)).verdict == "improved"


def test_min_rate_drop_is_configurable() -> None:
    assert (
        compare(agg_binary(3, 5), agg_binary(2, 5), min_rate_drop=0.1).verdict
        == "regressed"
    )


def test_a_drop_exactly_at_the_threshold_is_never_a_regression() -> None:
    """4/5→3/5 and 3/5→2/5 are both one extra failure; float must not split them."""
    assert compare(agg_binary(4, 5), agg_binary(3, 5)).verdict == "unchanged"
    assert compare(agg_binary(3, 5), agg_binary(2, 5)).verdict == "unchanged"
    assert compare(agg_binary(9, 10), agg_binary(7, 10)).verdict == "unchanged"


# -- continuous rule -------------------------------------------------------


def test_inside_the_observed_band_is_not_a_regression() -> None:
    """The central claim. If this fails the project has no reason to exist."""
    baseline = agg_continuous([0.50, 0.60, 0.55, 0.58, 0.52])
    candidate = agg_continuous([0.51, 0.57, 0.53, 0.59, 0.54])
    assert compare(baseline, candidate).verdict == "unchanged"


def test_below_the_observed_floor_is_a_regression() -> None:
    baseline = agg_continuous([0.50, 0.60, 0.55, 0.58, 0.52])
    candidate = agg_continuous([0.30, 0.32, 0.31, 0.29, 0.30])
    assert compare(baseline, candidate).verdict == "regressed"


def test_min_effect_can_suppress_a_tiny_drop() -> None:
    baseline = agg_continuous([0.50, 0.51])
    candidate = agg_continuous([0.49, 0.49])
    assert compare(baseline, candidate).verdict == "regressed"
    assert compare(baseline, candidate, min_effect=0.1).verdict == "unchanged"


def test_lower_is_better_reverses_the_comparison() -> None:
    kw = {"direction": "lower_is_better"}
    fast = agg_continuous([1.0, 1.1, 1.2], **kw)
    slow = agg_continuous([4.0, 4.1, 4.2], **kw)
    assert compare(fast, slow).verdict == "regressed"
    assert compare(slow, fast).verdict == "improved"


def test_a_continuous_scorers_pass_rate_also_counts() -> None:
    """Value inside the band but the bound now fails — still a regression."""
    baseline = aggregate_case([[continuous(0.5, passed=True)] for _ in range(5)])["0:n"]
    candidate = aggregate_case([[continuous(0.5, passed=False)] for _ in range(5)])[
        "0:n"
    ]
    assert compare(baseline, candidate).verdict == "regressed"


# -- refusing to guess -----------------------------------------------------


def test_a_single_repeat_is_unmeasured_not_significant() -> None:
    assert compare(agg_binary(1, 1), agg_binary(0, 1)).verdict == "unmeasured"


def test_unreliable_scorers_are_skipped() -> None:
    unreliable = aggregate_case(
        [[replace(continuous(1.0), reliable=False)] for _ in range(5)]
    )["0:n"]
    reliable = agg_continuous([9.0] * 5)
    assert compare(unreliable, reliable).verdict == "skipped"


def test_the_reason_carries_the_numbers() -> None:
    reason = compare(agg_binary(5, 5), agg_binary(3, 5)).reason
    assert "5/5" in reason and "3/5" in reason
