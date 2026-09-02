import sys
import textwrap
from pathlib import Path

import pytest

from noisefloor.config import Paths
from noisefloor.suite import Suite, load_suite

FAKE = str(Path(__file__).parent / "fake_target.py")


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(tmp_path / ".noisefloor")


@pytest.fixture
def suite_factory(tmp_path: Path):
    def make(body: str, *, name: str = "suite.yaml") -> Suite:
        path = tmp_path / name
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return load_suite(path)

    return make


@pytest.fixture
def simple_suite(suite_factory):
    """One case, one always-passing scorer, backed by the fake target."""
    return suite_factory(
        f"""
        name: demo
        target:
          command: ["{sys.executable}", "{FAKE}", "{{{{input}}}}"]
          timeout_s: 20
        defaults:
          repeats: 3
        cases:
          - id: alpha
            input: a question
            scorers:
              - json_valid
              - {{type: json_path_equals, path: confidence, value: high}}
        """
    )
