"""Suite definition: what to run, on which inputs, scored how.

YAML rather than TOML because case inputs are prompts, and block scalars are the
only pleasant way to write a multi-line prompt in a config file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from noisefloor.config import DEFAULT_REPEATS, DEFAULT_TIMEOUT_S


class SuiteError(ValueError):
    """A suite file that cannot be loaded or does not make sense."""


class ScorerSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str

    def params(self) -> dict[str, Any]:
        return dict(self.__pydantic_extra__ or {})

    def canonical(self) -> dict[str, Any]:
        return {"type": self.type, **self.params()}


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1)
    cwd: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S
    env: dict[str, str] = Field(default_factory=dict)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Case ids become directory names under `.noisefloor/runs/`, so they are
    #: constrained rather than sanitised — a silently rewritten id would break
    #: the matching that the whole diff depends on.
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    input: str
    repeats: int | None = Field(default=None, ge=1)
    scorers: list[ScorerSpec] = Field(min_length=1)

    def definition_hash(self) -> str:
        """Identity of what this case *means*, so a silent edit cannot hide.

        Deliberately excludes ``repeats``: running a case more times sharpens the
        measurement without changing the thing being measured.
        """
        payload = json.dumps(
            {"input": self.input, "scorers": [s.canonical() for s in self.scorers]},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class _Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repeats: int = Field(default=DEFAULT_REPEATS, ge=1)


class _SuiteFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    target: TargetSpec
    defaults: _Defaults = Field(default_factory=_Defaults)
    cases: list[Case] = Field(min_length=1)


class Suite:
    def __init__(self, model: _SuiteFile, source_path: Path) -> None:
        self.name = model.name
        self.target = model.target
        self.cases = model.cases
        self.default_repeats = model.defaults.repeats
        self.source_path = source_path
        self.source_dir = source_path.parent
        self.case_by_id = {case.id: case for case in model.cases}

    def repeats_for(self, case: Case) -> int:
        return case.repeats if case.repeats is not None else self.default_repeats

    def suite_hash(self) -> str:
        payload = json.dumps(
            {
                "name": self.name,
                "target": self.target.model_dump(),
                "cases": {c.id: c.definition_hash() for c in self.cases},
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @property
    def target_cwd(self) -> Path:
        """`cwd` is resolved against the suite file so a suite is portable."""
        if self.target.cwd is None:
            return self.source_dir
        return (self.source_dir / self.target.cwd).resolve()


def _normalise_scorers(raw: Any) -> Any:
    """`- json_valid` is sugar for `- {type: json_valid}`."""
    if not isinstance(raw, dict):
        return raw
    for case in raw.get("cases") or []:
        if not isinstance(case, dict):
            continue
        scorers = case.get("scorers")
        if isinstance(scorers, list):
            case["scorers"] = [
                {"type": s} if isinstance(s, str) else s for s in scorers
            ]
    return raw


def load_suite(path: Path) -> Suite:
    if not path.is_file():
        raise SuiteError(f"suite not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SuiteError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SuiteError(f"{path}: expected a mapping at the top level")

    try:
        model = _SuiteFile.model_validate(_normalise_scorers(raw))
    except ValidationError as exc:
        raise SuiteError(f"{path}: {exc}") from exc

    seen: set[str] = set()
    for case in model.cases:
        if case.id in seen:
            raise SuiteError(f"{path}: duplicate case id {case.id!r}")
        seen.add(case.id)

    return Suite(model, path.resolve())
