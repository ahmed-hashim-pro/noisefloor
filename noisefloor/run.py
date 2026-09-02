"""Execute a suite, persist everything, and re-score without re-running.

Raw stdout is stored for every repeat. That is what makes a corrected scorer
free — ``rescore`` replays the stored outputs — and what lets the harness test
its own diff and report layers against committed fixtures with no subprocess.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from noisefloor import __version__
from noisefloor.config import Paths
from noisefloor.scoring import ScoreResult, build_all, score_invocation
from noisefloor.suite import Suite
from noisefloor.target import Invocation, invoke


class RunRecordError(ValueError):
    """A stored run cannot be reloaded because its files are inconsistent."""


@dataclass(frozen=True)
class CaseRun:
    case_id: str
    definition_hash: str
    invocations: list[Invocation]
    scores: list[list[ScoreResult]]

    @property
    def ok_count(self) -> int:
        return sum(1 for inv in self.invocations if inv.ok)

    @property
    def outcome(self) -> Literal["ok", "degraded", "error"]:
        if self.ok_count == 0:
            return "error"
        return "ok" if self.ok_count == len(self.invocations) else "degraded"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    suite_name: str
    suite_hash: str
    target_command: list[str]
    target_cwd: str
    git_sha: str | None
    repeats: int
    jobs: int
    started_at: str
    finished_at: str
    harness_version: str
    cases: list[CaseRun]

    @property
    def case_by_id(self) -> dict[str, CaseRun]:
        return {case.case_id: case for case in self.cases}

    def save(self, paths: Paths) -> Path:
        paths.ensure()
        run_dir = paths.run_dir(self.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        meta = {k: v for k, v in asdict(self).items() if k != "cases"} | {
            "cases": [
                {
                    "case_id": c.case_id,
                    "definition_hash": c.definition_hash,
                    "outcome": c.outcome,
                    "repeats": len(c.invocations),
                }
                for c in self.cases
            ]
        }
        _write_json(run_dir / "run.json", meta)

        scores: dict[str, list[list[dict]]] = {}
        for case in self.cases:
            case_dir = paths.case_dir(self.run_id, case.case_id)
            case_dir.mkdir(parents=True, exist_ok=True)
            # Re-executing a suite at the same explicit started= (e.g. a
            # frozen clock in tests) reuses a run id even at microsecond
            # resolution. Without this, higher-numbered repeat files from the
            # earlier write would survive and outnumber the new scores.json
            # entries.
            for stale in case_dir.glob("*.json"):
                stale.unlink()
            for inv in case.invocations:
                _write_json(case_dir / f"{inv.repeat}.json", asdict(inv))
            scores[case.case_id] = [
                [asdict(r) for r in repeat] for repeat in case.scores
            ]
        _write_json(run_dir / "scores.json", scores)
        return run_dir

    @classmethod
    def load(cls, paths: Paths, run_id: str) -> RunRecord:
        run_dir = paths.run_dir(run_id)
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))

        cases: list[CaseRun] = []
        for entry in meta["cases"]:
            case_id = entry["case_id"]
            case_dir = paths.case_dir(run_id, case_id)
            invocations = [
                Invocation(**json.loads(p.read_text(encoding="utf-8")))
                for p in sorted(case_dir.glob("*.json"), key=_repeat_index)
            ]
            case_scores = scores.get(case_id, [])
            if len(case_scores) != len(invocations):
                raise RunRecordError(
                    f"run {run_id!r} case {case_id!r}: {len(invocations)} "
                    f"invocation(s) but {len(case_scores)} score list(s) — "
                    "the stored run is inconsistent"
                )
            cases.append(
                CaseRun(
                    case_id=case_id,
                    definition_hash=entry["definition_hash"],
                    invocations=invocations,
                    scores=[
                        [ScoreResult(**r) for r in repeat] for repeat in case_scores
                    ],
                )
            )
        return cls(**{k: v for k, v in meta.items() if k != "cases"}, cases=cases)


def make_run_id(suite: Suite, started: datetime) -> str:
    stamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{suite.name}-{suite.suite_hash()[:8]}"


def execute(
    suite: Suite,
    *,
    paths: Paths,
    repeats: int | None = None,
    jobs: int = 1,
    started: datetime | None = None,
) -> RunRecord:
    started = started or datetime.now(UTC)
    concurrent = jobs > 1
    cwd = suite.target_cwd

    units: list[tuple[int, int]] = []
    plans = []
    for case_index, case in enumerate(suite.cases):
        n = repeats if repeats is not None else suite.repeats_for(case)
        plans.append((case, build_all(case, concurrent=concurrent), n))
        units.extend((case_index, r) for r in range(n))

    def run_unit(unit: tuple[int, int]) -> tuple[int, Invocation]:
        case_index, repeat = unit
        case, _, _ = plans[case_index]
        return case_index, invoke(suite.target, case, repeat, cwd=cwd)

    if concurrent:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(run_unit, units))
    else:
        results = [run_unit(unit) for unit in units]

    collected: dict[int, list[Invocation]] = {}
    for case_index, inv in results:
        collected.setdefault(case_index, []).append(inv)

    cases = []
    for case_index, (case, scorers, _) in enumerate(plans):
        invocations = sorted(collected.get(case_index, []), key=lambda i: i.repeat)
        cases.append(
            CaseRun(
                case_id=case.id,
                definition_hash=case.definition_hash(),
                invocations=invocations,
                scores=[score_invocation(scorers, inv) for inv in invocations],
            )
        )

    effective = repeats if repeats is not None else suite.default_repeats
    return RunRecord(
        run_id=make_run_id(suite, started),
        suite_name=suite.name,
        suite_hash=suite.suite_hash(),
        target_command=list(suite.target.command),
        target_cwd=suite.target.cwd or ".",
        git_sha=_git_sha(cwd),
        repeats=effective,
        jobs=jobs,
        started_at=started.astimezone(UTC).isoformat(),
        finished_at=datetime.now(UTC).isoformat(),
        harness_version=__version__,
        cases=cases,
    )


def rescore(record: RunRecord, suite: Suite) -> RunRecord:
    """Re-apply the suite's scorers to already-captured output."""
    concurrent = record.jobs > 1
    cases = []
    for case_run in record.cases:
        case = suite.case_by_id.get(case_run.case_id)
        if case is None:
            cases.append(case_run)
            continue
        scorers = build_all(case, concurrent=concurrent)
        cases.append(
            replace(
                case_run,
                definition_hash=case.definition_hash(),
                scores=[score_invocation(scorers, i) for i in case_run.invocations],
            )
        )
    return replace(record, cases=cases)


def set_baseline(paths: Paths, suite_name: str, run_id: str) -> Path:
    paths.ensure()
    path = paths.baseline_file(suite_name)
    _write_json(path, {"run_id": run_id, "set_at": datetime.now(UTC).isoformat()})
    return path


def get_baseline(paths: Paths, suite_name: str) -> str | None:
    path = paths.baseline_file(suite_name)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["run_id"]


def latest_run_id(paths: Paths, suite_name: str | None = None) -> str | None:
    if not paths.runs.is_dir():
        return None
    candidates = sorted(p.name for p in paths.runs.iterdir() if p.is_dir())
    if suite_name is not None:
        candidates = [c for c in candidates if _suite_name_of(c) == suite_name]
    return candidates[-1] if candidates else None


def _suite_name_of(run_id: str) -> str | None:
    """Recover the suite name from a run id of the form
    ``<stamp>-<suite_name>-<hash>``.

    The stamp is ``%Y%m%dT%H%M%S.%fZ`` and the hash is hex, so neither can
    contain a hyphen — but ``Suite.name`` has no such restriction, so a
    plain substring check (``f"-{suite_name}-" in run_id``) would wrongly
    match e.g. suite "demo" against a run id for suite "demo-extra". Split
    off the hyphen-free stamp and hash first instead.
    """
    parts = run_id.split("-", 1)
    if len(parts) < 2 or "-" not in parts[1]:
        return None
    return parts[1].rsplit("-", 1)[0]


def _repeat_index(path: Path) -> int:
    return int(path.stem)


def _write_json(path: Path, payload: object) -> None:
    """Atomic so a crash mid-write cannot leave a half-written record."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _git_sha(cwd: Path) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
