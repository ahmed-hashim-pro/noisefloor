"""Defaults and the on-disk layout. Every tunable in the harness lives here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_REPEATS = 5
DEFAULT_TIMEOUT_S = 60.0

#: A binary scorer regresses when the pass rate drops by more than this. At the
#: default N=5 that means one extra failure is tolerated from an already-flaky
#: baseline, but two are not. See the spec, section 6.3.
DEFAULT_MIN_RATE_DROP = 0.2

#: Continuous scorers must also move by more than this to count. Zero means only
#: the observed-range clause binds, which is the right default until you know
#: what size of change you care about.
DEFAULT_MIN_EFFECT = 0.0

DEFAULT_ROOT = Path(".noisefloor")


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def baselines(self) -> Path:
        return self.root / "baselines"

    def run_dir(self, run_id: str) -> Path:
        return self.runs / run_id

    def case_dir(self, run_id: str, case_id: str) -> Path:
        return self.run_dir(run_id) / "cases" / case_id

    def baseline_file(self, suite_name: str) -> Path:
        return self.baselines / f"{suite_name}.json"

    def ensure(self) -> None:
        self.runs.mkdir(parents=True, exist_ok=True)
        self.baselines.mkdir(parents=True, exist_ok=True)
