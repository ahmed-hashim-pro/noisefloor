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


def test_unanimous_candidate_from_a_flaky_baseline_is_unchanged() -> None:
    """The unanimous clause is anchored to the baseline, not mirrored onto
    the candidate: a baseline that already showed variance (4/5) makes a
    5/5 candidate unremarkable, not an improvement. Old behaviour mirrored
    the clause and reported `improved` here -- that was the defect; the
    0.200 rate delta is identical to the 3/5 -> 2/5 case below, which reports
    `unchanged`, so the verdict must not hinge on which side lands on
    unanimity."""
    assert compare(agg_binary(4, 5), agg_binary(5, 5)).verdict == "unchanged"


def test_min_rate_drop_is_configurable() -> None:
    assert (
        compare(agg_binary(3, 5), agg_binary(2, 5), min_rate_drop=0.1).verdict
        == "regressed"
    )


def test_a_drop_of_exactly_one_fifth_is_never_a_regression() -> None:
    """A one-fifth drop must get the same verdict from every N that produces it."""
    assert compare(agg_binary(4, 5), agg_binary(3, 5)).verdict == "unchanged"
    assert compare(agg_binary(3, 5), agg_binary(2, 5)).verdict == "unchanged"
    assert compare(agg_binary(9, 10), agg_binary(7, 10)).verdict == "unchanged"


def test_a_gain_of_exactly_one_fifth_is_never_flagged_as_improved() -> None:
    """The mirror of the threshold case: swapped args need the same tolerance."""
    assert compare(agg_binary(3, 5), agg_binary(4, 5)).verdict == "unchanged"


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


def test_an_effect_of_exactly_min_effect_is_never_a_regression() -> None:
    """An effect equal to min_effect must get the same verdict at every magnitude."""
    tolerated_baseline = agg_continuous([0.5, 0.5])
    tolerated_candidate = agg_continuous([0.4, 0.4])
    assert (
        compare(tolerated_baseline, tolerated_candidate, min_effect=0.1).verdict
        == "unchanged"
    )

    flagged_baseline = agg_continuous([1.1, 1.1])
    flagged_candidate = agg_continuous([1.0, 1.0])
    assert (
        compare(flagged_baseline, flagged_candidate, min_effect=0.1).verdict
        == "unchanged"
    )


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
    result = compare(baseline, candidate)
    assert result.verdict == "regressed"
    # The unanimous-baseline clause fired on pass rate, not on the value (both
    # sides are 0.5), so the reason must carry the pass counts, not a mean/range
    # band that would make identical values look like the "evidence."
    assert "5/5" in result.reason and "0/5" in result.reason


def test_continuous_rate_drop_reports_pass_counts_not_a_value_band() -> None:
    """Same fix, via the rate-drop clause rather than the unanimous one."""
    baseline = aggregate_case(
        [[continuous(0.5, passed=p)] for p in [True, True, True, False, False]]
    )["0:n"]
    candidate = aggregate_case(
        [[continuous(0.5, passed=p)] for p in [True, False, False, False, False]]
    )["0:n"]
    result = compare(baseline, candidate)
    assert result.verdict == "regressed"
    assert "3/5" in result.reason and "1/5" in result.reason


# -- baseline is the fixed reference for the continuous range test ---------
#
# A degenerate *baseline* band is the strongest possible noise estimate
# (zero observed spread), not the absence of one -- a difference from it
# can't be noise. A degenerate *candidate* band, by contrast, says nothing
# about the baseline's noise and must never be used as the reference. The
# four cases below pin exactly that asymmetry.


def test_baseline_spread_candidate_point_is_unchanged() -> None:
    """The real incident, reproduced verbatim: retrieval is deterministic, so
    the candidate emits one citation every repeat and has zero observed
    spread. The baseline (which has real spread) is the only side the range
    test may reference, so the candidate's degeneracy must not matter."""
    baseline = agg_continuous([0.5965, 0.4374, 0.5965])
    candidate = agg_continuous([0.5965, 0.5965, 0.5965])
    assert compare(baseline, candidate).verdict == "unchanged"


def test_both_sides_deterministic_large_drop_is_regressed() -> None:
    """A perfectly reproducible system whose value collapses is a genuine
    regression -- the zero-width baseline band is exactly what makes the
    move undeniable, not what excuses it."""
    baseline = agg_continuous([0.9, 0.9, 0.9])
    candidate = agg_continuous([0.1, 0.1, 0.1])
    assert compare(baseline, candidate).verdict == "regressed"


def test_both_sides_deterministic_large_rise_is_improved() -> None:
    """Mirror of the case above, on the good side of `direction`."""
    baseline = agg_continuous([0.1, 0.1, 0.1])
    candidate = agg_continuous([0.9, 0.9, 0.9])
    assert compare(baseline, candidate).verdict == "improved"


def test_baseline_spread_candidate_genuinely_collapsed_is_regressed() -> None:
    """The fourth point in the acceptance matrix: a real collapse is still
    caught when the baseline itself has spread."""
    baseline = agg_continuous([0.50, 0.60, 0.55])
    candidate = agg_continuous([0.05, 0.05, 0.05])
    assert compare(baseline, candidate).verdict == "regressed"


def test_an_explicit_min_effect_still_gates_a_move_from_a_deterministic_baseline() -> (
    None
):
    """min_effect is checked before the range test runs, so it still applies
    exactly as documented even when the baseline is degenerate."""
    baseline = agg_continuous([0.9, 0.9, 0.9])
    candidate = agg_continuous([0.1, 0.1, 0.1])
    assert compare(baseline, candidate, min_effect=0.2).verdict == "regressed"
    assert compare(candidate, baseline, min_effect=0.2).verdict == "improved"


def test_min_effect_from_a_deterministic_baseline_still_respects_direction() -> None:
    """A large effect from a degenerate baseline must not fire on an
    *improving* move just because the magnitude clears min_effect."""
    baseline = agg_continuous([0.1, 0.1, 0.1])
    candidate = agg_continuous([0.9, 0.9, 0.9])
    assert compare(baseline, candidate, min_effect=0.2).verdict == "improved"
    assert compare(candidate, baseline, min_effect=0.2).verdict == "regressed"


def test_min_effect_can_suppress_a_tiny_move_from_a_deterministic_baseline() -> None:
    """ "Ignore drops under X" still means what it says when the baseline
    happens to be a point rather than a range."""
    baseline = agg_continuous([0.50, 0.50, 0.50])
    candidate = agg_continuous([0.49, 0.49, 0.49])
    assert compare(baseline, candidate).verdict == "regressed"
    assert compare(baseline, candidate, min_effect=0.1).verdict == "unchanged"


# -- an improved reason reads baseline-to-candidate, like everything else --


def test_a_regressed_continuous_reason_reads_baseline_to_candidate() -> None:
    """Locks in the direction of the unswapped call, so the mirrored fix
    below can't silently break it."""
    baseline = agg_continuous([0.50, 0.60, 0.55])
    candidate = agg_continuous([0.30, 0.32, 0.31])
    reason = compare(baseline, candidate).reason
    assert reason.index("0.55") < reason.index("0.31")
    assert "outside the baseline band" in reason


def test_an_improved_continuous_reason_reads_baseline_to_candidate() -> None:
    """The reported reason must narrate baseline → candidate. The band it
    cites is always the baseline's -- there is no swapped call to produce a
    "candidate band" version of this clause -- so "outside the baseline
    band" is correct in both directions, unlike the two binary clauses."""
    baseline = agg_continuous([0.30, 0.32, 0.31])
    candidate = agg_continuous([0.70, 0.72, 0.71])
    result = compare(baseline, candidate)
    assert result.verdict == "improved"
    assert result.reason.index("0.31") < result.reason.index("0.71")
    assert "outside the baseline band [0.300–0.320]" in result.reason


def test_an_improved_rate_drop_reason_says_gain_not_drop() -> None:
    baseline, candidate = agg_binary(1, 5), agg_binary(4, 5)
    result = compare(baseline, candidate)
    assert result.verdict == "improved"
    assert result.reason.index("1/5") < result.reason.index("4/5")
    assert "gain > 0.20" in result.reason
    assert "drop" not in result.reason


def test_a_unanimous_candidate_reason_is_not_reported_as_improved() -> None:
    """The mirrored "candidate is now unanimous" reason text no longer
    exists: unanimity only ever fires from the baseline side, so a 4/5 -> 5/5
    move is `unchanged`, not `improved`, and carries the ordinary
    within-noise reason."""
    baseline, candidate = agg_binary(4, 5), agg_binary(5, 5)
    result = compare(baseline, candidate)
    assert result.verdict == "unchanged"
    assert "the candidate is now unanimous" not in result.reason


# -- acceptance matrix: unanimous clause anchored to the baseline (#7) -----
#
# Same 0.200 rate delta, opposite verdicts, decided only by which side
# happened to land on unanimity -- that was the defect. These five rows
# pin the corrected matrix: unanimity fires only from the baseline, and
# the rate-drop clause alone carries the symmetric improvement direction.


def test_clean_baseline_any_failure_is_regressed() -> None:
    """5/5 -> 4/5: clean baseline, any failure is new -- unchanged behaviour."""
    assert compare(agg_binary(5, 5), agg_binary(4, 5)).verdict == "regressed"


def test_flaky_baseline_unanimous_candidate_is_unchanged() -> None:
    """4/5 -> 5/5: the change -- baseline had variance, a 5/5 candidate is
    unremarkable."""
    assert compare(agg_binary(4, 5), agg_binary(5, 5)).verdict == "unchanged"


def test_large_rate_gain_is_improved_via_the_rate_drop_clause() -> None:
    """2/5 -> 5/5: 0.600 delta, rate-drop clause."""
    assert compare(agg_binary(2, 5), agg_binary(5, 5)).verdict == "improved"


def test_small_rate_drop_from_a_flaky_baseline_is_unchanged() -> None:
    """3/5 -> 2/5: unchanged behaviour."""
    assert compare(agg_binary(3, 5), agg_binary(2, 5)).verdict == "unchanged"


def test_rate_gain_of_one_third_is_improved_via_the_rate_drop_clause() -> None:
    """2/3 -> 3/3: 0.333 delta, rate-drop clause."""
    assert compare(agg_binary(2, 3), agg_binary(3, 3)).verdict == "improved"


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
