"""Run the target as a subprocess and classify what came back.

The command is an argv list and the process is spawned with ``shell=False``.
That is a security property, not a style choice: case inputs are arbitrary text,
and a shell string would turn every case into a command-injection vector.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noisefloor.suite import Case, TargetSpec

OUTCOMES = ("ok", "error:exit", "error:timeout", "error:parse")

_PLACEHOLDER = re.compile(r"\{\{(input|case_id|repeat)\}\}")


@dataclass(frozen=True)
class Invocation:
    case_id: str
    repeat: int
    argv: list[str]
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    outcome: str
    parsed: Any | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


def render_argv(
    command: list[str], *, case_id: str, case_input: str, repeat: int
) -> list[str]:
    # Single pass: chained str.replace calls re-scan already-substituted text,
    # so case input containing "{{repeat}}" would get replaced a second time.
    # Case input is untrusted and must pass through as inert data, not template.
    values = {"input": case_input, "case_id": case_id, "repeat": str(repeat)}
    return [
        _PLACEHOLDER.sub(lambda match: values[match.group(1)], element)
        for element in command
    ]


def invoke(spec: TargetSpec, case: Case, repeat: int, *, cwd: Path) -> Invocation:
    argv = render_argv(
        spec.command, case_id=case.id, case_input=case.input, repeat=repeat
    )
    started = time.monotonic()

    def finish(**kwargs: Any) -> Invocation:
        return Invocation(
            case_id=case.id,
            repeat=repeat,
            argv=argv,
            duration_s=time.monotonic() - started,
            **kwargs,
        )

    try:
        completed = subprocess.run(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=cwd,
            env=_env(spec),
            capture_output=True,
            text=True,
            timeout=spec.timeout_s,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return finish(
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr),
            exit_code=None,
            outcome="error:timeout",
        )
    except OSError as exc:
        return finish(stdout="", stderr=str(exc), exit_code=None, outcome="error:exit")

    if completed.returncode != 0:
        return finish(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            outcome="error:exit",
        )

    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return finish(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=0,
            outcome="error:parse",
        )

    return finish(
        stdout=completed.stdout,
        stderr=completed.stderr,
        exit_code=0,
        outcome="ok",
        parsed=parsed,
    )


def _env(spec: TargetSpec) -> dict[str, str] | None:
    if not spec.env:
        return None
    import os

    return {**os.environ, **spec.env}


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
