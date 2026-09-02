import json
import sys
from pathlib import Path

import pytest

from noisefloor.cli import main
from noisefloor.config import Paths
from noisefloor.run import RunRecord, get_baseline, latest_run_id

FAKE = str(Path(__file__).parent / "fake_target.py")


def write_suite(tmp_path: Path, *extra_args: str, needle: str = "twelve") -> Path:
    args = ", ".join(f'"{a}"' for a in extra_args)
    path = tmp_path / "suite.yaml"
    path.write_text(
        f"""
name: demo
target:
  command: ["{sys.executable}", "{FAKE}", {args + ", " if args else ""}"{{{{input}}}}"]
  timeout_s: 20
defaults:
  repeats: 3
cases:
  - id: alpha
    input: a question
    scorers:
      - json_valid
      - {{type: contains, needle: {needle}, path: answer}}
""",
        encoding="utf-8",
    )
    return path


def write_env_suite(tmp_path: Path, env: dict[str, str] | None = None) -> Path:
    """Same target command every time; only `env` varies. Proves the CLI's
    real run-to-run wiring catches a regression, not just the escape hatch
    that `write_suite`'s command-embedded extra args require."""
    env_block = ""
    if env:
        pairs = "\n".join(f"    {key}: {value!r}" for key, value in env.items())
        env_block = f"  env:\n{pairs}\n"
    path = tmp_path / "env_suite.yaml"
    path.write_text(
        f"""
name: demo
target:
  command: ["{sys.executable}", "{FAKE}", "{{{{input}}}}"]
  timeout_s: 20
{env_block}defaults:
  repeats: 3
cases:
  - id: alpha
    input: a question
    scorers:
      - json_valid
      - {{type: contains, needle: twelve, path: answer}}
""",
        encoding="utf-8",
    )
    return path


def run(tmp_path: Path, *argv: str) -> int:
    """`--root` is a top-level option, so it goes before the subcommand."""
    return main(["--root", str(tmp_path / ".noisefloor"), *argv])


# -- happy path ------------------------------------------------------------


def test_run_records_a_run(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "run", str(write_suite(tmp_path))) == 0
    assert (tmp_path / ".noisefloor" / "runs").is_dir()
    assert "3 repeats" in capsys.readouterr().out


def test_first_check_adopts_a_baseline_and_says_so(tmp_path: Path, capsys) -> None:
    """Exit 0 here means "nothing was compared", not "nothing regressed"."""
    assert run(tmp_path, "check", str(write_suite(tmp_path))) == 0
    err = capsys.readouterr().err
    assert "no baseline" in err
    assert "adopted" in err


def test_second_check_against_an_unchanged_target_passes(tmp_path: Path) -> None:
    """Both checks must record distinct runs, so exit 0 reflects a genuine
    unchanged-target comparison rather than a run diffed against itself."""
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    paths = Paths(tmp_path / ".noisefloor")
    first_id = latest_run_id(paths)

    assert run(tmp_path, "check", str(suite)) == 0

    second_id = latest_run_id(paths)
    assert second_id != first_id


def test_a_real_regression_exits_one(tmp_path: Path) -> None:
    """The helper bakes its extra args into the target command itself, so the
    recorded command necessarily differs between baseline and candidate here;
    --allow-target-change lets the regression under test be what decides the
    exit code instead."""
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    degraded = write_suite(tmp_path, "--answer", "something else entirely")
    assert run(tmp_path, "check", str(degraded), "--allow-target-change") == 1


def test_partial_degradation_is_surfaced_not_silently_unchanged(
    tmp_path: Path, capsys
) -> None:
    """A candidate that errors on some repeats but not all must say so, even
    though it is not `broke` and the exit code does not change for it alone."""
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite), "--repeats", "5")
    degraded = write_suite(tmp_path, "--fail-every", "2", "--repeat", "{{repeat}}")
    capsys.readouterr()

    code = run(
        tmp_path, "check", str(degraded), "--allow-target-change", "--repeats", "5"
    )
    out = capsys.readouterr().out

    assert code == 0  # 2 of 5 ok repeats still all pass; not a regression
    assert "2/5 repeats ok in the candidate, down from 5/5" in out
    assert "case 'alpha' degraded:" in out


def test_a_broken_target_exits_two(tmp_path: Path) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    broken = write_suite(tmp_path, "--exit-code", "1")
    assert run(tmp_path, "check", str(broken), "--allow-target-change") == 2


def test_a_regression_with_an_unchanged_command_exits_one(tmp_path: Path) -> None:
    """The primary scenario: target command byte-identical, behavior behind it
    changed via env. Exercises execute -> save -> get_baseline -> diff_runs ->
    _emit without the --allow-target-change escape hatch."""
    suite = write_env_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    degraded = write_env_suite(
        tmp_path, env={"NOISEFLOOR_FAKE_ANSWER": "something else entirely"}
    )
    assert run(tmp_path, "check", str(degraded)) == 1


def test_a_break_with_an_unchanged_command_exits_two(tmp_path: Path) -> None:
    suite = write_env_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    broken = write_env_suite(tmp_path, env={"NOISEFLOOR_FAKE_EXIT_CODE": "1"})
    assert run(tmp_path, "check", str(broken)) == 2


# -- errors ----------------------------------------------------------------


def test_a_missing_suite_exits_three(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "run", str(tmp_path / "nope.yaml")) == 3
    assert "not found" in capsys.readouterr().err


def test_an_unknown_scorer_exits_three(tmp_path: Path, capsys) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        'name: d\ntarget: {command: ["true"]}\n'
        "cases:\n  - id: a\n    input: x\n    scorers: [{type: vibes}]\n",
        encoding="utf-8",
    )
    assert run(tmp_path, "run", str(path)) == 3
    assert "unknown scorer" in capsys.readouterr().err


def test_diff_without_a_baseline_exits_three(tmp_path: Path, capsys) -> None:
    run(tmp_path, "run", str(write_suite(tmp_path)))
    assert run(tmp_path, "diff") == 3
    assert "baseline" in capsys.readouterr().err.lower()


def test_diff_of_a_run_against_itself_exits_three(tmp_path: Path, capsys) -> None:
    """The baseline for this suite *is* this run, so comparing it against
    itself must be refused rather than silently reported as unchanged."""
    suite = write_env_suite(tmp_path)
    run(tmp_path, "run", str(suite), "--set-baseline")
    paths = Paths(tmp_path / ".noisefloor")
    run_id = latest_run_id(paths)

    assert run(tmp_path, "diff", run_id) == 3
    assert "same run" in capsys.readouterr().err


def test_a_corrupted_run_record_exits_three_not_a_traceback(
    tmp_path: Path, capsys
) -> None:
    """A configuration error must never look like a clean run — including one
    discovered only when a stored run is reloaded."""
    suite = write_suite(tmp_path)
    run(tmp_path, "run", str(suite))
    paths = Paths(tmp_path / ".noisefloor")
    run_id = latest_run_id(paths)
    (paths.case_dir(run_id, "alpha") / "2.json").unlink()

    assert run(tmp_path, "show", run_id) == 3
    assert "inconsistent" in capsys.readouterr().err


# -- plumbing --------------------------------------------------------------


def test_reports_go_to_stdout_and_diagnostics_to_stderr(tmp_path: Path, capsys) -> None:
    """`noisefloor diff --format json | jq` must work without filtering."""
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    capsys.readouterr()
    run(tmp_path, "check", str(suite), "--format", "json")
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "{" not in captured.err


def test_json_format_is_parseable(tmp_path: Path, capsys) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    capsys.readouterr()
    run(tmp_path, "check", str(suite), "--format", "json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 0


def test_run_set_baseline_reports_distinctly_from_first_check_adoption(
    tmp_path: Path, capsys
) -> None:
    """`run --set-baseline`'s message must never be mistaken for `check`'s
    first-baseline-adoption message, and must actually set the pointer."""
    suite = write_suite(tmp_path)
    assert run(tmp_path, "run", str(suite), "--set-baseline") == 0
    err = capsys.readouterr().err
    assert "baseline for 'demo' set to" in err
    assert "no baseline" not in err
    assert "adopted" not in err

    paths = Paths(tmp_path / ".noisefloor")
    assert get_baseline(paths, "demo") == latest_run_id(paths)


def test_diff_command_compares_a_specific_run_against_the_baseline(
    tmp_path: Path, capsys
) -> None:
    """The standalone `diff <run-id>` path, not `check`'s implicit one."""
    suite = write_env_suite(tmp_path)
    run(tmp_path, "run", str(suite), "--set-baseline")
    paths = Paths(tmp_path / ".noisefloor")
    baseline_id = latest_run_id(paths)

    degraded = write_env_suite(
        tmp_path, env={"NOISEFLOOR_FAKE_ANSWER": "something else entirely"}
    )
    run(tmp_path, "run", str(degraded))
    candidate_id = latest_run_id(paths)
    capsys.readouterr()

    assert run(tmp_path, "diff", candidate_id) == 1
    out = capsys.readouterr().out
    assert baseline_id in out
    assert candidate_id in out


def test_scorers_lists_every_builtin(tmp_path: Path, capsys) -> None:
    assert run(tmp_path, "scorers") == 0
    out = capsys.readouterr().out
    for name in ("json_valid", "contains", "json_path_subset", "latency"):
        assert name in out


def test_show_prints_stored_output(tmp_path: Path, capsys) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "run", str(suite))
    capsys.readouterr()
    assert run(tmp_path, "show", "--case", "alpha") == 0
    assert "confidence" in capsys.readouterr().out


def test_rescore_updates_scores_without_rerunning(tmp_path: Path, capsys) -> None:
    """The cache paying off: a corrected scorer costs no API calls."""
    suite = write_suite(tmp_path)
    run(tmp_path, "run", str(suite))
    capsys.readouterr()
    paths = Paths(tmp_path / ".noisefloor")
    run_id = latest_run_id(paths)
    before = RunRecord.load(paths, run_id)

    fixed = write_suite(tmp_path, needle="parks")
    assert run(tmp_path, "rescore", "--suite", str(fixed)) == 0

    after = RunRecord.load(paths, run_id)
    assert after.cases[0].scores != before.cases[0].scores
    # "did not rerun" means the stored raw output — including timing, which a
    # fresh subprocess invocation could not reproduce exactly — is untouched.
    assert after.cases[0].invocations == before.cases[0].invocations


def test_baseline_show_lists_pointers(tmp_path: Path, capsys) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    capsys.readouterr()
    assert run(tmp_path, "baseline", "show") == 0
    out = capsys.readouterr().out
    assert any(line.startswith("demo:") for line in out.splitlines())


@pytest.mark.parametrize("args", [["run"], ["diff", "--format", "xml"]])
def test_bad_usage_does_not_traceback(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *args)
    assert exc.value.code == 2  # argparse's own usage error
