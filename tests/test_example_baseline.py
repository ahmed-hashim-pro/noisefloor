"""Guards the committed real-world baseline under examples/rag-knowledge-agent/.

Everything here must work with no network and no API key: that is the whole
point of storing raw captured output. The suite's `target.cwd` points at a
sibling `rag-knowledge-agent` checkout that will not exist on most machines,
so nothing in this file may require the target to actually run.
"""

from __future__ import annotations

from pathlib import Path

from noisefloor.config import Paths
from noisefloor.diff import diff_runs
from noisefloor.report import FORMATS, render
from noisefloor.run import RunRecord, get_baseline
from noisefloor.scoring import validate_suite
from noisefloor.suite import load_suite

EXAMPLE_DIR = Path(__file__).parent.parent / "examples" / "rag-knowledge-agent"
SUITE_PATH = EXAMPLE_DIR / "suite.yaml"
BASELINE_ROOT = EXAMPLE_DIR / "baseline"

FORBIDDEN_SNIPPETS = ("/Users/", "/home/", "sk-ant")


def test_suite_loads_and_validates_without_a_runnable_target() -> None:
    suite = load_suite(SUITE_PATH)
    validate_suite(suite)
    assert suite.name == "rag-knowledge-agent"


def _load_baseline_record() -> RunRecord:
    paths = Paths(BASELINE_ROOT)
    run_id = get_baseline(paths, "rag-knowledge-agent")
    assert run_id is not None
    return RunRecord.load(paths, run_id)


def test_committed_baseline_loads_with_invocations() -> None:
    record = _load_baseline_record()
    assert record.cases
    for case in record.cases:
        assert case.invocations


def test_baseline_renders_in_every_report_format() -> None:
    record = _load_baseline_record()
    diff = diff_runs(record, record)
    for fmt in FORMATS:
        assert render(diff, fmt).strip()


def test_baseline_files_contain_no_absolute_paths_or_usernames() -> None:
    json_files = sorted(BASELINE_ROOT.rglob("*.json"))
    assert json_files, "expected committed baseline JSON files"
    for path in json_files:
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text, f"{path} contains {snippet!r}"
