from pathlib import Path

import pytest

from noisefloor.suite import SuiteError, load_suite

SUITE = """
name: demo
target:
  command: ["echo", "{{input}}"]
  cwd: ../thing
  timeout_s: 12
defaults:
  repeats: 3
cases:
  - id: one
    input: |
      a multi-line
      question
    scorers:
      - json_valid
      - {type: contains, needle: hello}
  - id: two
    input: short
    repeats: 7
    scorers: [json_valid]
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "suite.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_cases_and_target(tmp_path: Path) -> None:
    s = load_suite(write(tmp_path, SUITE))
    assert s.name == "demo"
    assert s.target.command == ["echo", "{{input}}"]
    assert s.target.timeout_s == 12
    assert [c.id for c in s.cases] == ["one", "two"]


def test_bare_string_scorer_is_normalised(tmp_path: Path) -> None:
    s = load_suite(write(tmp_path, SUITE))
    assert s.cases[0].scorers[0].type == "json_valid"
    assert s.cases[0].scorers[1].params() == {"needle": "hello"}


def test_repeats_fall_back_to_the_suite_default(tmp_path: Path) -> None:
    s = load_suite(write(tmp_path, SUITE))
    assert s.repeats_for(s.cases[0]) == 3
    assert s.repeats_for(s.cases[1]) == 7


def test_source_dir_anchors_relative_cwd(tmp_path: Path) -> None:
    """`cwd` in the suite is relative to the suite file, not the shell."""
    s = load_suite(write(tmp_path, SUITE))
    assert s.source_dir == tmp_path


def test_definition_hash_changes_with_input(tmp_path: Path) -> None:
    before = load_suite(write(tmp_path, SUITE)).cases[0].definition_hash()
    after = load_suite(write(tmp_path, SUITE.replace("short", "changed"))).cases[0]
    assert after.definition_hash() == before  # case "one" is untouched


def test_definition_hash_is_per_case(tmp_path: Path) -> None:
    original = load_suite(write(tmp_path, SUITE))
    edited = load_suite(write(tmp_path, SUITE.replace("short", "changed")))
    assert edited.cases[1].definition_hash() != original.cases[1].definition_hash()


def test_scorer_order_is_part_of_the_hash(tmp_path: Path) -> None:
    swapped = SUITE.replace(
        "      - json_valid\n      - {type: contains, needle: hello}",
        "      - {type: contains, needle: hello}\n      - json_valid",
    )
    original = load_suite(write(tmp_path, SUITE)).cases[0].definition_hash()
    assert load_suite(write(tmp_path, swapped)).cases[0].definition_hash() != original


def test_case_ids_must_be_filename_safe(tmp_path: Path) -> None:
    """Ids become directory names; a rewritten id would break case matching."""
    with pytest.raises(SuiteError):
        load_suite(write(tmp_path, SUITE.replace("id: two", "id: ../escape")))


def test_duplicate_case_ids_are_rejected(tmp_path: Path) -> None:
    bad = SUITE.replace("id: two", "id: one")
    with pytest.raises(SuiteError, match="duplicate case id"):
        load_suite(write(tmp_path, bad))


def test_empty_command_is_rejected(tmp_path: Path) -> None:
    bad = SUITE.replace('command: ["echo", "{{input}}"]', "command: []")
    with pytest.raises(SuiteError):
        load_suite(write(tmp_path, bad))


def test_missing_file_is_a_suite_error(tmp_path: Path) -> None:
    with pytest.raises(SuiteError, match="not found"):
        load_suite(tmp_path / "absent.yaml")


def test_malformed_yaml_is_a_suite_error(tmp_path: Path) -> None:
    with pytest.raises(SuiteError):
        load_suite(write(tmp_path, "name: [unclosed"))
