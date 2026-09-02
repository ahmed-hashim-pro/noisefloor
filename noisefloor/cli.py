"""Command line entry point.

Reports go to stdout and diagnostics to stderr, so `noisefloor diff --format
json | jq` works without filtering. Exit codes: 0 clean, 1 regression, 2 broke,
3 harness or configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from noisefloor import __version__
from noisefloor.config import DEFAULT_MIN_EFFECT, DEFAULT_MIN_RATE_DROP, Paths
from noisefloor.diff import TargetChanged, diff_runs
from noisefloor.report import FORMATS, render, render_run_summary
from noisefloor.run import (
    RunRecord,
    RunRecordError,
    execute,
    get_baseline,
    latest_run_id,
    rescore,
    set_baseline,
)
from noisefloor.scoring import describe, validate_suite
from noisefloor.suite import SuiteError, load_suite

CONFIG_ERROR = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="noisefloor",
        description="Measure the noise before calling anything a regression.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--root", type=Path, default=None, help="storage root (default .noisefloor)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_thresholds(p: argparse.ArgumentParser) -> None:
        p.add_argument("--min-rate-drop", type=float, default=DEFAULT_MIN_RATE_DROP)
        p.add_argument("--min-effect", type=float, default=DEFAULT_MIN_EFFECT)
        p.add_argument("--allow-target-change", action="store_true")
        p.add_argument("--format", choices=FORMATS, default="terminal")

    run_p = sub.add_parser("run", help="execute a suite and record the result")
    run_p.add_argument("suite", type=Path)
    run_p.add_argument("--repeats", type=int, default=None)
    run_p.add_argument("--jobs", type=int, default=1)
    run_p.add_argument("--set-baseline", action="store_true")

    check_p = sub.add_parser("check", help="run, then diff against the baseline")
    check_p.add_argument("suite", type=Path)
    check_p.add_argument("--repeats", type=int, default=None)
    check_p.add_argument("--jobs", type=int, default=1)
    add_thresholds(check_p)

    diff_p = sub.add_parser("diff", help="compare a recorded run to the baseline")
    diff_p.add_argument("run_id", nargs="?", default=None)
    diff_p.add_argument("--baseline", default=None)
    add_thresholds(diff_p)

    base_p = sub.add_parser("baseline", help="manage baseline pointers")
    base_sub = base_p.add_subparsers(dest="baseline_command", required=True)
    set_p = base_sub.add_parser("set")
    set_p.add_argument("run_id")
    base_sub.add_parser("show")

    rescore_p = sub.add_parser("rescore", help="re-apply scorers to stored output")
    rescore_p.add_argument("run_id", nargs="?", default=None)
    rescore_p.add_argument("--suite", type=Path, required=True)

    show_p = sub.add_parser("show", help="inspect stored output")
    show_p.add_argument("run_id", nargs="?", default=None)
    show_p.add_argument("--case", default=None)

    sub.add_parser("scorers", help="list the built-in scorers")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = Paths(args.root or Path(".noisefloor"))
    try:
        return _dispatch(args, paths)
    except (SuiteError, TargetChanged, FileNotFoundError, RunRecordError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return CONFIG_ERROR


def _dispatch(args: argparse.Namespace, paths: Paths) -> int:
    match args.command:
        case "run":
            return _cmd_run(args, paths)
        case "check":
            return _cmd_check(args, paths)
        case "diff":
            return _cmd_diff(args, paths)
        case "baseline":
            return _cmd_baseline(args, paths)
        case "rescore":
            return _cmd_rescore(args, paths)
        case "show":
            return _cmd_show(args, paths)
        case "scorers":
            return _cmd_scorers()
    raise AssertionError(f"unhandled command {args.command!r}")


def _load(path: Path):
    suite = load_suite(path)
    validate_suite(suite)
    return suite


def _cmd_run(args: argparse.Namespace, paths: Paths) -> int:
    suite = _load(args.suite)
    record = execute(suite, paths=paths, repeats=args.repeats, jobs=args.jobs)
    record.save(paths)
    print(render_run_summary(record))
    if args.set_baseline:
        set_baseline(paths, record.suite_name, record.run_id)
        print(
            f"baseline for {record.suite_name!r} set to {record.run_id}",
            file=sys.stderr,
        )
    return 0


def _cmd_check(args: argparse.Namespace, paths: Paths) -> int:
    suite = _load(args.suite)
    record = execute(suite, paths=paths, repeats=args.repeats, jobs=args.jobs)
    record.save(paths)

    baseline_id = get_baseline(paths, suite.name)
    if baseline_id is None:
        set_baseline(paths, suite.name, record.run_id)
        print(render_run_summary(record))
        print(
            f"no baseline for {suite.name!r} yet — adopted {record.run_id} as the "
            "baseline. Re-run check after a change to compare against it.",
            file=sys.stderr,
        )
        return 0

    baseline = RunRecord.load(paths, baseline_id)
    return _emit(_diff(baseline, record, args), args.format)


def _cmd_diff(args: argparse.Namespace, paths: Paths) -> int:
    run_id = args.run_id or latest_run_id(paths)
    if run_id is None:
        print("error: no recorded runs", file=sys.stderr)
        return CONFIG_ERROR
    candidate = RunRecord.load(paths, run_id)

    baseline_id = args.baseline or get_baseline(paths, candidate.suite_name)
    if baseline_id is None:
        print(
            f"error: no baseline for {candidate.suite_name!r}. "
            f"Run: noisefloor baseline set <run-id>",
            file=sys.stderr,
        )
        return CONFIG_ERROR
    if baseline_id == run_id:
        print("error: candidate and baseline are the same run", file=sys.stderr)
        return CONFIG_ERROR

    baseline = RunRecord.load(paths, baseline_id)
    return _emit(_diff(baseline, candidate, args), args.format)


def _diff(baseline: RunRecord, candidate: RunRecord, args: argparse.Namespace):
    return diff_runs(
        baseline,
        candidate,
        allow_target_change=args.allow_target_change,
        min_rate_drop=args.min_rate_drop,
        min_effect=args.min_effect,
    )


def _emit(diff, fmt: str) -> int:
    print(render(diff, fmt))
    return diff.exit_code


def _cmd_baseline(args: argparse.Namespace, paths: Paths) -> int:
    if args.baseline_command == "set":
        record = RunRecord.load(paths, args.run_id)
        set_baseline(paths, record.suite_name, record.run_id)
        print(f"{record.suite_name}: {record.run_id}")
        return 0

    if not paths.baselines.is_dir() or not any(paths.baselines.glob("*.json")):
        print("no baselines recorded", file=sys.stderr)
        return CONFIG_ERROR
    for pointer in sorted(paths.baselines.glob("*.json")):
        data = json.loads(pointer.read_text(encoding="utf-8"))
        print(f"{pointer.stem}: {data['run_id']}  (set {data['set_at']})")
    return 0


def _cmd_rescore(args: argparse.Namespace, paths: Paths) -> int:
    suite = _load(args.suite)
    run_id = args.run_id or latest_run_id(paths, suite.name)
    if run_id is None:
        print("error: no recorded runs", file=sys.stderr)
        return CONFIG_ERROR
    updated = rescore(RunRecord.load(paths, run_id), suite)
    updated.save(paths)
    print(render_run_summary(updated))
    return 0


def _cmd_show(args: argparse.Namespace, paths: Paths) -> int:
    run_id = args.run_id or latest_run_id(paths)
    if run_id is None:
        print("error: no recorded runs", file=sys.stderr)
        return CONFIG_ERROR
    record = RunRecord.load(paths, run_id)
    print(render_run_summary(record))
    for case in record.cases:
        if args.case is not None and case.case_id != args.case:
            continue
        print(f"\n== {case.case_id} ({case.outcome})")
        for inv in case.invocations:
            print(f"-- repeat {inv.repeat} [{inv.outcome}] {inv.duration_s:.2f}s")
            print(inv.stdout.rstrip())
    return 0


def _cmd_scorers() -> int:
    print(f"{'scorer':<20} {'kind':<11} parameters")
    for name, kind, summary in describe():
        print(f"{name:<20} {kind:<11} {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
