import json
import sys
from pathlib import Path

from noisefloor.suite import Case, ScorerSpec, TargetSpec
from noisefloor.target import invoke, render_argv

FAKE = str(Path(__file__).parent / "fake_target.py")


def case(text: str = "hello", case_id: str = "c1") -> Case:
    return Case(id=case_id, input=text, scorers=[ScorerSpec(type="json_valid")])


def spec(*extra: str, timeout_s: float = 10.0) -> TargetSpec:
    return TargetSpec(
        command=[sys.executable, FAKE, *extra, "{{input}}"], timeout_s=timeout_s
    )


# -- templating ------------------------------------------------------------


def test_input_is_substituted_into_one_element() -> None:
    argv = render_argv(
        ["cmd", "--q={{input}}"], case_id="c", case_input="a b", repeat=0
    )
    assert argv == ["cmd", "--q=a b"]


def test_case_id_is_substituted() -> None:
    assert render_argv(["{{case_id}}"], case_id="c1", case_input="", repeat=0) == ["c1"]


def test_repeat_is_substituted() -> None:
    """Lets a target vary per repeat, which is how tests build a known rate."""
    assert render_argv(["{{repeat}}"], case_id="c", case_input="", repeat=3) == ["3"]


def test_shell_metacharacters_stay_inside_one_argument(tmp_path: Path) -> None:
    """A shell string here would make case input a command-injection vector."""
    nasty = '; rm -rf ~ && echo "pwned" `whoami` $(id)'
    argv = render_argv(["cmd", "{{input}}"], case_id="c", case_input=nasty, repeat=0)
    assert argv == ["cmd", nasty]


def test_no_shell_is_used_end_to_end(tmp_path: Path) -> None:
    nasty = "; touch /tmp/nf_should_not_exist"
    result = invoke(spec("--echo-argv"), case(nasty), 0, cwd=tmp_path)
    assert json.loads(result.stdout)["argv"][-1] == nasty


# -- outcomes --------------------------------------------------------------


def test_successful_run_is_ok_and_parsed(tmp_path: Path) -> None:
    result = invoke(spec(), case(), 0, cwd=tmp_path)
    assert result.outcome == "ok"
    assert result.ok
    assert result.parsed["confidence"] == "high"
    assert result.duration_s >= 0


def test_non_zero_exit_is_error_exit(tmp_path: Path) -> None:
    result = invoke(spec("--exit-code", "3"), case(), 0, cwd=tmp_path)
    assert result.outcome == "error:exit"
    assert result.exit_code == 3
    assert not result.ok


def test_unparseable_stdout_is_error_parse(tmp_path: Path) -> None:
    result = invoke(spec("--garbage"), case(), 0, cwd=tmp_path)
    assert result.outcome == "error:parse"
    assert result.parsed is None


def test_timeout_is_error_timeout(tmp_path: Path) -> None:
    result = invoke(spec("--delay", "5", timeout_s=0.3), case(), 0, cwd=tmp_path)
    assert result.outcome == "error:timeout"
    assert result.exit_code is None


def test_stderr_is_captured_separately(tmp_path: Path) -> None:
    result = invoke(spec("--stderr", "loading model"), case(), 0, cwd=tmp_path)
    assert "loading model" in result.stderr
    assert result.outcome == "ok"


def test_missing_executable_is_error_exit_not_a_crash(tmp_path: Path) -> None:
    result = invoke(
        TargetSpec(command=["definitely-not-a-real-binary-xyz"]),
        case(),
        0,
        cwd=tmp_path,
    )
    assert result.outcome == "error:exit"
    assert "definitely-not-a-real-binary-xyz" in result.stderr
