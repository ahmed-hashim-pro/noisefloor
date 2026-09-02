import pytest

from noisefloor.scoring import build, build_all, score_invocation, validate_suite
from noisefloor.suite import Case, ScorerSpec, SuiteError, load_suite
from noisefloor.target import Invocation

PAYLOAD = {
    "answer": "The robot safe-parks after twelve minutes offline.",
    "confidence": "high",
    "citations": [
        {"source": "a.md", "score": 0.7},
        {"source": "b.md", "score": 0.4},
    ],
}


def inv(parsed=PAYLOAD, *, outcome="ok", duration=1.5) -> Invocation:
    import json

    return Invocation(
        case_id="c",
        repeat=0,
        argv=["x"],
        stdout=json.dumps(parsed) if parsed is not None else "boom",
        stderr="",
        exit_code=0,
        duration_s=duration,
        outcome=outcome,
        parsed=parsed,
    )


def score(type_: str, **params):
    return build(ScorerSpec(type=type_, **params), 0).score(inv())


# -- binary scorers --------------------------------------------------------


def test_json_valid_passes_on_parsed_output() -> None:
    assert score("json_valid").passed


def test_contains_is_case_insensitive_by_default() -> None:
    assert score("contains", needle="SAFE-PARKS").passed


def test_contains_honours_case_sensitive() -> None:
    assert not score("contains", needle="SAFE-PARKS", case_sensitive=True).passed


def test_contains_can_target_a_path() -> None:
    assert score("contains", needle="twelve", path="answer").passed
    assert not score("contains", needle="twelve", path="confidence").passed


def test_not_contains_inverts() -> None:
    assert score("not_contains", needle="parental leave").passed
    assert not score("not_contains", needle="twelve").passed


def test_regex_matches() -> None:
    assert score("regex", pattern=r"twelve\s+minutes").passed


def test_json_path_equals() -> None:
    assert score("json_path_equals", path="confidence", value="high").passed
    assert not score("json_path_equals", path="confidence", value="low").passed


def test_json_path_equals_fails_when_path_is_absent() -> None:
    assert not score("json_path_equals", path="missing", value="high").passed


def test_json_path_in() -> None:
    assert score("json_path_in", path="confidence", values=["high", "medium"]).passed
    assert not score("json_path_in", path="confidence", values=["low"]).passed


def test_json_path_subset() -> None:
    ok = score("json_path_subset", path="citations[].source", allowed=["a.md", "b.md"])
    bad = score("json_path_subset", path="citations[].source", allowed=["a.md"])
    assert ok.passed and not bad.passed


def test_json_path_subset_of_nothing_passes() -> None:
    """An empty set is a subset of everything; a refusal cites nothing."""
    empty = build(
        ScorerSpec(type="json_path_subset", path="citations[].source", allowed=["z"]),
        0,
    ).score(inv({"citations": []}))
    assert empty.passed


def test_binary_results_are_zero_or_one() -> None:
    assert score("json_valid").value == 1.0
    assert score("contains", needle="nope").value == 0.0


# -- continuous scorers ----------------------------------------------------


def test_json_path_number_takes_the_worst_value_by_default() -> None:
    result = score("json_path_number", path="citations[].score", min=0.5)
    assert result.kind == "continuous"
    assert result.value == pytest.approx(0.4)
    assert not result.passed


def test_json_path_number_aggregate_mean() -> None:
    result = score("json_path_number", path="citations[].score", aggregate="mean")
    assert result.value == pytest.approx(0.55)


def test_latency_is_lower_is_better() -> None:
    result = score("latency", max_s=1.0)
    assert result.direction == "lower_is_better"
    assert result.value == pytest.approx(1.5)
    assert not result.passed


def test_latency_is_unreliable_under_concurrency() -> None:
    """Contended timings are not measurements, and must not be treated as such."""
    concurrent = build(ScorerSpec(type="latency"), 0, concurrent=True).score(inv())
    serial = build(ScorerSpec(type="latency"), 0, concurrent=False).score(inv())
    assert not concurrent.reliable
    assert serial.reliable


def test_only_latency_is_affected_by_concurrency() -> None:
    result = build(ScorerSpec(type="json_valid"), 0, concurrent=True).score(inv())
    assert result.reliable


# -- errors are not scored -------------------------------------------------


def test_errored_invocations_produce_no_scores() -> None:
    scorers = build_all(Case(id="c", input="", scorers=[ScorerSpec(type="json_valid")]))
    assert score_invocation(scorers, inv(None, outcome="error:parse")) == []


# -- validation ------------------------------------------------------------


def test_unknown_scorer_type_is_rejected() -> None:
    with pytest.raises(SuiteError, match="unknown scorer"):
        build(ScorerSpec(type="vibes"), 0)


def test_missing_required_parameter_is_rejected() -> None:
    with pytest.raises(SuiteError, match="contains"):
        build(ScorerSpec(type="contains"), 0)


def test_unexpected_parameter_is_rejected() -> None:
    with pytest.raises(SuiteError):
        build(ScorerSpec(type="json_valid", nedle="typo"), 0)


def test_malformed_path_is_rejected_at_build_time() -> None:
    with pytest.raises(SuiteError):
        build(ScorerSpec(type="json_path_equals", path="a..b", value=1), 0)


def test_validate_suite_reports_the_offending_case(tmp_path) -> None:
    path = tmp_path / "s.yaml"
    path.write_text(
        'name: s\ntarget: {command: ["true"]}\n'
        "cases:\n  - id: bad\n    input: x\n    scorers: [{type: vibes}]\n",
        encoding="utf-8",
    )
    with pytest.raises(SuiteError, match="bad"):
        validate_suite(load_suite(path))


def test_keys_are_index_prefixed() -> None:
    case = Case(
        id="c",
        input="",
        scorers=[ScorerSpec(type="json_valid"), ScorerSpec(type="json_valid")],
    )
    assert [s.key for s in build_all(case)] == ["0:json_valid", "1:json_valid"]
