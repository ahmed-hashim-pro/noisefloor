import json
import re

import pytest

from noisefloor.diff import diff_runs
from noisefloor.report import FORMATS, render, render_run_summary
from tests.test_diff import case, record


@pytest.fixture
def sample():
    return diff_runs(
        record(case("a", 5, 5), case("b", 5, 5), case("c", 5, 5), run_id="base"),
        record(
            case("a", 5, 5), case("b", 1, 5), case("c", 0, 5, ok=False), run_id="cand"
        ),
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_renders_without_error(sample, fmt: str) -> None:
    assert render(sample, fmt).strip()


def test_terminal_output_names_the_failing_cases(sample) -> None:
    """ "c" and "b" are common letters that show up inside "base"/"cand"/
    "broke" regardless of whether the case ids are rendered, so this checks
    for them as standalone tokens rather than bare substrings."""
    text = render(sample, "terminal")
    assert "broke" in text and "regressed" in text
    assert re.search(r"\bb\b", text) and re.search(r"\bc\b", text)


def test_terminal_output_shows_the_numbers_not_just_the_verdict(sample) -> None:
    """The rule is a heuristic; the reader must be able to disagree with it."""
    assert "5/5" in render(sample, "terminal")
    assert "1/5" in render(sample, "terminal")


def test_json_output_is_machine_readable(sample) -> None:
    payload = json.loads(render(sample, "json"))
    assert payload["exit_code"] == 2
    assert payload["baseline_run_id"] == "base"
    verdicts = {c["case_id"]: c["verdict"] for c in payload["cases"]}
    assert verdicts == {"a": "unchanged", "b": "regressed", "c": "broke"}


def test_json_output_keeps_non_ascii_readable(sample) -> None:
    assert "\\u" not in render(sample, "json")


def test_markdown_output_is_a_table(sample) -> None:
    text = render(sample, "markdown")
    assert text.count("|") > 6
    assert "---" in text


def test_warnings_are_rendered(sample) -> None:
    d = diff_runs(
        record(case("a", 5, 5), run_id="base"),
        record(case("a", 5, 5), run_id="cand", command=["different"]),
        allow_target_change=True,
    )
    assert "target command" in render(d, "terminal")


def test_added_removed_and_redefined_cases_render_in_every_format() -> None:
    d = diff_runs(
        record(case("x", 5, 5, dh="h1"), case("y", 5, 5), run_id="base"),
        record(case("x", 5, 5, dh="h2"), case("z", 5, 5), run_id="cand"),
    )
    verdicts = {c.case_id: c.verdict for c in d.cases}
    assert verdicts == {"x": "redefined", "y": "removed", "z": "added"}
    for fmt in FORMATS:
        assert render(d, fmt).strip()


def test_an_unknown_format_is_rejected(sample) -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render(sample, "xml")


def test_run_summary_reports_outcomes() -> None:
    text = render_run_summary(record(case("a", 5, 5), case("b", 0, 5, ok=False)))
    assert "2 cases" in text
    assert "error" in text
