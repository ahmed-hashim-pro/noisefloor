import json
import re
from typing import get_args

import pytest

from noisefloor.diff import CaseVerdict, diff_runs
from noisefloor.report import _MARK, FORMATS, render, render_run_summary
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


def test_markdown_output_is_a_table_with_evidence_in_every_row(sample) -> None:
    """Every data row must carry real evidence, not just a note or nothing:
    an unchanged case has no note and no flagged reason, so it depends on the
    band summary alone to avoid a blank detail cell."""
    text = render(sample, "markdown")
    assert "---" in text
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert len(rows) == 3
    for row in rows:
        detail = row.split("|")[3].strip()
        assert detail, f"case row has no evidence: {row!r}"
    assert "5/5" in text and "1/5" in text


def test_json_output_includes_the_scorer_bands(sample) -> None:
    payload = json.loads(render(sample, "json"))
    b = next(c for c in payload["cases"] if c["case_id"] == "b")
    scorer = b["scorers"][0]
    assert scorer["baseline"] == "5/5"
    assert scorer["candidate"] == "1/5"


def test_warnings_are_rendered() -> None:
    d = diff_runs(
        record(case("a", 5, 5), run_id="base"),
        record(case("a", 5, 5), run_id="cand", command=["different"]),
        allow_target_change=True,
    )
    assert "target command" in render(d, "terminal")


def test_added_removed_and_redefined_cases_are_named_in_every_format() -> None:
    d = diff_runs(
        record(case("x", 5, 5, dh="h1"), case("y", 5, 5), run_id="base"),
        record(case("x", 5, 5, dh="h2"), case("z", 5, 5), run_id="cand"),
    )
    verdicts = {c.case_id: c.verdict for c in d.cases}
    assert verdicts == {"x": "redefined", "y": "removed", "z": "added"}
    for fmt in FORMATS:
        text = render(d, fmt)
        for case_id in ("x", "y", "z"):
            assert case_id in text
    payload = json.loads(render(d, "json"))
    assert len(payload["cases"]) == 3


def test_an_unknown_format_is_rejected(sample) -> None:
    with pytest.raises(ValueError, match="unknown format"):
        render(sample, "xml")


def test_run_summary_reports_outcomes() -> None:
    text = render_run_summary(record(case("a", 5, 5), case("b", 0, 5, ok=False)))
    assert "2 cases" in text
    assert "error" in text


def test_run_summary_reports_actual_per_case_repeat_counts() -> None:
    """A suite where one case overrides `repeats` must not be summarized with
    the suite-level default alone — that number can be false for the other
    case. `record()` sets `RunRecord.repeats` from the first case only (2
    here), so the old code would have printed "2 cases × 2 repeats", falsely
    claiming case "b"'s 5 repeats as 2."""
    text = render_run_summary(record(case("a", 2, 2), case("b", 5, 5)))
    assert "2-5 repeats" in text


def test_mark_covers_every_case_verdict() -> None:
    """A verdict added to CaseVerdict without a matching _MARK entry would
    KeyError at render time; this pins the two in sync."""
    assert set(_MARK) == set(get_args(CaseVerdict))
