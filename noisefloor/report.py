"""Rendering. Verdicts are never shown without the numbers behind them."""

from __future__ import annotations

import json
from collections import Counter

from noisefloor.diff import CaseDiff, Diff
from noisefloor.run import RunRecord

FORMATS = ("terminal", "json", "markdown")

_MARK = {
    "broke": "BROKE",
    "regressed": "REGRESSED",
    "redefined": "redefined",
    "unmeasured": "unmeasured",
    "removed": "removed",
    "added": "added",
    "fixed": "fixed",
    "improved": "improved",
    "unchanged": "ok",
}


def render(diff: Diff, fmt: str) -> str:
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; expected one of {FORMATS}")
    return {
        "terminal": _terminal,
        "json": _json,
        "markdown": _markdown,
    }[fmt](diff)


def _headline(diff: Diff) -> str:
    counts = Counter(c.verdict for c in diff.cases)
    parts = [f"{n} {verdict}" for verdict, n in sorted(counts.items())]
    return ", ".join(parts) if parts else "no cases"


def _terminal(diff: Diff) -> str:
    lines = [
        f"{diff.suite_name}: {diff.baseline_run_id} → {diff.candidate_run_id}",
        f"  {_headline(diff)}",
        "",
    ]
    for case in diff.cases:
        lines.append(f"{_MARK[case.verdict]:>10}  {case.case_id}")
        if case.note:
            lines.append(f"            {case.note}")
        for sig in case.scorers:
            if sig.verdict == "unchanged":
                continue
            lines.append(f"            {sig.key}: {sig.reason}")
        lines.extend(_bands(case))
    if diff.warnings:
        lines.append("")
        lines.extend(f"warning: {w}" for w in diff.warnings)
    lines.append("")
    lines.append(f"exit {diff.exit_code}")
    return "\n".join(lines)


def _bands(case: CaseDiff) -> list[str]:
    lines = []
    for key in sorted(set(case.baseline) | set(case.candidate)):
        before = case.baseline.get(key)
        after = case.candidate.get(key)
        lines.append(
            f"            {key}: "
            f"{before.band if before else '—'} → {after.band if after else '—'}"
        )
    return lines


def _json(diff: Diff) -> str:
    payload = {
        "suite": diff.suite_name,
        "baseline_run_id": diff.baseline_run_id,
        "candidate_run_id": diff.candidate_run_id,
        "exit_code": diff.exit_code,
        "warnings": diff.warnings,
        "cases": [
            {
                "case_id": c.case_id,
                "verdict": c.verdict,
                "note": c.note,
                "scorers": [
                    {
                        "key": s.key,
                        "verdict": s.verdict,
                        "reason": s.reason,
                        "baseline": c.baseline[s.key].band
                        if s.key in c.baseline
                        else None,
                        "candidate": c.candidate[s.key].band
                        if s.key in c.candidate
                        else None,
                    }
                    for s in c.scorers
                ],
            }
            for c in diff.cases
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _markdown(diff: Diff) -> str:
    lines = [
        f"### {diff.suite_name}: `{diff.baseline_run_id}` → "
        f"`{diff.candidate_run_id}`",
        "",
        f"{_headline(diff)}",
        "",
        "| case | verdict | detail |",
        "| --- | --- | --- |",
    ]
    for case in diff.cases:
        detail = case.note or "; ".join(
            s.reason for s in case.scorers if s.verdict != "unchanged"
        )
        lines.append(f"| `{case.case_id}` | {case.verdict} | {detail} |")
    if diff.warnings:
        lines.extend(["", *[f"> warning: {w}" for w in diff.warnings]])
    return "\n".join(lines)


def render_run_summary(record: RunRecord) -> str:
    counts = Counter(c.outcome for c in record.cases)
    detail = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    return (
        f"{record.run_id}\n"
        f"  {len(record.cases)} cases × {record.repeats} repeats — {detail}"
    )
