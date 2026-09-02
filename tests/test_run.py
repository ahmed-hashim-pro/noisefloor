import json
import sys
from datetime import UTC, datetime

import pytest

from noisefloor.config import Paths
from noisefloor.run import (
    RunRecord,
    RunRecordError,
    execute,
    get_baseline,
    latest_run_id,
    rescore,
    set_baseline,
)
from tests.conftest import FAKE

FROZEN = datetime(2026, 9, 2, 10, 30, 0, tzinfo=UTC)


def test_every_case_runs_the_configured_repeats_and_outcome_is_ok(
    simple_suite, paths
) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    assert record.repeats == 3
    assert len(record.cases[0].invocations) == 3
    assert record.cases[0].outcome == "ok"


def test_repeats_can_be_overridden(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, repeats=2, started=FROZEN)
    assert len(record.cases[0].invocations) == 2


def test_run_id_is_self_describing(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    assert record.run_id.startswith("20260902T103000.000000Z-demo-")


def test_raw_output_is_persisted_per_repeat(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    record.save(paths)
    stored = paths.case_dir(record.run_id, "alpha") / "1.json"
    payload = json.loads(stored.read_text())
    assert payload["outcome"] == "ok"
    assert json.loads(payload["stdout"])["confidence"] == "high"


def test_a_saved_run_round_trips(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    record.save(paths)
    loaded = RunRecord.load(paths, record.run_id)
    assert loaded.suite_hash == record.suite_hash
    assert loaded.target_command == record.target_command
    assert [c.case_id for c in loaded.cases] == ["alpha"]
    assert loaded.cases[0].scores == record.cases[0].scores
    assert loaded.cases[0].invocations == record.cases[0].invocations


def test_target_cwd_is_stored_suite_relative(suite_factory, paths) -> None:
    """A committed run record must not carry an absolute path."""
    suite = suite_factory(
        f"""
        name: demo
        target: {{command: ["{sys.executable}", "{FAKE}"], cwd: "."}}
        cases:
          - id: a
            input: x
            scorers: [json_valid]
        """
    )
    record = execute(suite, paths=paths, repeats=2, started=FROZEN)
    assert record.target_cwd == "."


def test_errored_repeats_are_recorded_but_not_scored(suite_factory, paths) -> None:
    suite = suite_factory(
        f"""
        name: demo
        target: {{command: ["{sys.executable}", "{FAKE}", "--garbage"]}}
        cases:
          - id: a
            input: x
            scorers: [json_valid]
        """
    )
    record = execute(suite, paths=paths, repeats=3, started=FROZEN)
    case = record.cases[0]
    assert case.outcome == "error"
    assert case.ok_count == 0
    assert case.scores == [[], [], []]


def test_a_known_pass_rate_is_reproducible(suite_factory, paths) -> None:
    """Tasks 6 and 8 rest on being able to construct a specific pass rate."""
    suite = suite_factory(
        f"""
        name: demo
        target: {{command: ["{sys.executable}", "{FAKE}",
                  "--flake-every", "2", "--repeat", "{{{{repeat}}}}"]}}
        cases:
          - id: alpha
            input: x
            scorers: [{{type: json_path_equals, path: confidence, value: high}}]
        """
    )
    record = execute(suite, paths=paths, repeats=4, started=FROZEN)
    assert [s[0].passed for s in record.cases[0].scores] == [
        False,
        True,
        False,
        True,
    ]


def test_rescore_recomputes_from_stored_output(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    again = rescore(record, simple_suite)
    assert again.cases[0].scores == record.cases[0].scores
    assert again.cases[0].invocations == record.cases[0].invocations


def test_jobs_greater_than_one_marks_latency_unreliable(suite_factory, paths) -> None:
    suite = suite_factory(
        f"""
        name: demo
        target: {{command: ["{sys.executable}", "{FAKE}"]}}
        cases:
          - id: a
            input: x
            scorers: [latency]
        """
    )
    record = execute(suite, paths=paths, repeats=2, jobs=4, started=FROZEN)
    assert record.jobs == 4
    assert all(not s[0].reliable for s in record.cases[0].scores)


def test_baseline_pointer_round_trips(simple_suite, paths) -> None:
    record = execute(simple_suite, paths=paths, started=FROZEN)
    record.save(paths)
    set_baseline(paths, "demo", record.run_id)
    assert get_baseline(paths, "demo") == record.run_id


def test_no_baseline_yet_is_none(paths: Paths) -> None:
    paths.ensure()
    assert get_baseline(paths, "demo") is None


def test_latest_run_id_is_the_newest(simple_suite, paths) -> None:
    from datetime import timedelta

    first = execute(simple_suite, paths=paths, repeats=1, started=FROZEN)
    first.save(paths)
    second = execute(
        simple_suite, paths=paths, repeats=1, started=FROZEN + timedelta(minutes=5)
    )
    second.save(paths)
    assert latest_run_id(paths, "demo") == second.run_id


def test_latest_run_id_does_not_match_a_suite_name_that_is_a_substring(
    suite_factory, paths
) -> None:
    """A run id embeds the suite name raw, so "demo" must not match "demo-extra"."""
    from datetime import timedelta

    demo = suite_factory(
        f"""
        name: demo
        target: {{command: ["{sys.executable}", "{FAKE}"]}}
        cases:
          - id: a
            input: x
            scorers: [json_valid]
        """,
        name="demo.yaml",
    )
    demo_extra = suite_factory(
        f"""
        name: demo-extra
        target: {{command: ["{sys.executable}", "{FAKE}"]}}
        cases:
          - id: a
            input: x
            scorers: [json_valid]
        """,
        name="demo-extra.yaml",
    )
    demo_record = execute(demo, paths=paths, repeats=1, started=FROZEN)
    demo_record.save(paths)
    extra_record = execute(
        demo_extra, paths=paths, repeats=1, started=FROZEN + timedelta(minutes=5)
    )
    extra_record.save(paths)
    assert latest_run_id(paths, "demo") == demo_record.run_id


def test_reexecuting_the_same_run_id_with_fewer_repeats_leaves_no_stale_files(
    simple_suite, paths
) -> None:
    """A frozen clock reuses the same started=, and therefore the same run id,
    even at microsecond resolution."""
    first = execute(simple_suite, paths=paths, repeats=3, started=FROZEN)
    first.save(paths)
    second = execute(simple_suite, paths=paths, repeats=1, started=FROZEN)
    assert second.run_id == first.run_id
    second.save(paths)

    loaded = RunRecord.load(paths, second.run_id)
    assert len(loaded.cases[0].invocations) == 1
    assert len(loaded.cases[0].scores) == 1


def test_load_raises_when_invocation_and_score_counts_disagree(
    simple_suite, paths
) -> None:
    record = execute(simple_suite, paths=paths, repeats=1, started=FROZEN)
    record.save(paths)
    case_dir = paths.case_dir(record.run_id, "alpha")
    stray = json.loads((case_dir / "0.json").read_text())
    (case_dir / "1.json").write_text(json.dumps({**stray, "repeat": 1}))

    with pytest.raises(RunRecordError):
        RunRecord.load(paths, record.run_id)
