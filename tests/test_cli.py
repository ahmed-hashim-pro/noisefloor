import json
import sys
from pathlib import Path

import pytest

from noisefloor.cli import main

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
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    assert run(tmp_path, "check", str(suite)) == 0


def test_a_real_regression_exits_one(tmp_path: Path) -> None:
    """The helper bakes its extra args into the target command itself, so the
    recorded command necessarily differs between baseline and candidate here;
    --allow-target-change lets the regression under test be what decides the
    exit code instead."""
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    degraded = write_suite(tmp_path, "--answer", "something else entirely")
    assert run(tmp_path, "check", str(degraded), "--allow-target-change") == 1


def test_a_broken_target_exits_two(tmp_path: Path) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    broken = write_suite(tmp_path, "--exit-code", "1")
    assert run(tmp_path, "check", str(broken), "--allow-target-change") == 2


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
    fixed = write_suite(tmp_path, needle="parks")
    assert run(tmp_path, "rescore", "--suite", str(fixed)) == 0


def test_baseline_show_lists_pointers(tmp_path: Path, capsys) -> None:
    suite = write_suite(tmp_path)
    run(tmp_path, "check", str(suite))
    capsys.readouterr()
    assert run(tmp_path, "baseline", "show") == 0
    assert "demo" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["run"], ["diff", "--format", "xml"]])
def test_bad_usage_does_not_traceback(tmp_path: Path, args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, *args)
    assert exc.value.code == 2  # argparse's own usage error
