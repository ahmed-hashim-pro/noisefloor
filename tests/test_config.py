from pathlib import Path

from noisefloor.config import DEFAULT_REPEATS, Paths


def test_defaults_match_the_spec() -> None:
    assert DEFAULT_REPEATS == 5


def test_paths_are_derived_from_one_root(tmp_path: Path) -> None:
    paths = Paths(tmp_path / ".noisefloor")
    assert paths.run_dir("r1") == tmp_path / ".noisefloor" / "runs" / "r1"
    assert paths.case_dir("r1", "c1") == paths.run_dir("r1") / "cases" / "c1"
    assert paths.baseline_file("s") == tmp_path / ".noisefloor" / "baselines" / "s.json"


def test_ensure_creates_the_tree(tmp_path: Path) -> None:
    paths = Paths(tmp_path / ".noisefloor")
    paths.ensure()
    assert paths.runs.is_dir()
    assert paths.baselines.is_dir()
