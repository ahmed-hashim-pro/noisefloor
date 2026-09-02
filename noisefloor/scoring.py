"""Deterministic scorers. No model calls, so the harness never needs a key.

Every scorer emits the same shape — a float value and a boolean pass — because
the significance rules in :mod:`noisefloor.stats` need both: the pass rate
carries binary scorers, the value carries continuous ones.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from noisefloor.jsonpath import JsonPath, JsonPathError
from noisefloor.suite import Case, ScorerSpec, Suite, SuiteError
from noisefloor.target import Invocation

ScorerKind = Literal["binary", "continuous"]
Direction = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True)
class ScoreResult:
    key: str
    type: str
    kind: ScorerKind
    direction: Direction
    value: float
    passed: bool
    reliable: bool = True
    detail: str = ""


class _Scorer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    KIND: ClassVar[ScorerKind] = "binary"
    DIRECTION: ClassVar[Direction] = "higher_is_better"
    SUMMARY: ClassVar[str] = ""

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        raise NotImplementedError

    def reliable_under_concurrency(self) -> bool:
        return True


REGISTRY: dict[str, type[_Scorer]] = {}


def _register(name: str):
    def wrap(cls: type[_Scorer]) -> type[_Scorer]:
        REGISTRY[name] = cls
        return cls

    return wrap


def _binary(passed: bool, detail: str = "") -> tuple[float, bool, str]:
    return (1.0 if passed else 0.0, passed, detail)


class _PathMixin(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def _parse(cls, value: str) -> str:
        JsonPath.parse(value)  # raises JsonPathError, surfaced as SuiteError
        return value

    def extract_values(self, inv: Invocation) -> list[Any]:
        return JsonPath.parse(self.path).extract(inv.parsed)


# -- binary ----------------------------------------------------------------


@_register("json_valid")
class JsonValid(_Scorer):
    SUMMARY: ClassVar[str] = "(no parameters) stdout parsed as JSON"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        return _binary(inv.parsed is not None)


class _TextScorer(_Scorer):
    path: str | None = None
    case_sensitive: bool = False

    @field_validator("path")
    @classmethod
    def _parse(cls, value: str | None) -> str | None:
        if value is not None:
            JsonPath.parse(value)
        return value

    def haystack(self, inv: Invocation) -> str:
        if self.path is None:
            text = inv.stdout
        else:
            found = JsonPath.parse(self.path).extract(inv.parsed)
            text = "\n".join(str(v) for v in found)
        return text if self.case_sensitive else text.lower()


@_register("contains")
class Contains(_TextScorer):
    needle: str
    SUMMARY: ClassVar[str] = "needle, [path], [case_sensitive]"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        needle = self.needle if self.case_sensitive else self.needle.lower()
        return _binary(needle in self.haystack(inv), f"needle={self.needle!r}")


@_register("not_contains")
class NotContains(Contains):
    SUMMARY: ClassVar[str] = "needle, [path], [case_sensitive]"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        value, passed, detail = super().evaluate(inv)
        return _binary(not passed, detail)


@_register("regex")
class Regex(_TextScorer):
    pattern: str
    SUMMARY: ClassVar[str] = "pattern, [path], [case_sensitive]"

    @field_validator("pattern")
    @classmethod
    def _compile(cls, value: str) -> str:
        re.compile(value)
        return value

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        flags = 0 if self.case_sensitive else re.IGNORECASE
        found = re.search(self.pattern, self.haystack(inv), flags) is not None
        return _binary(found, f"pattern={self.pattern!r}")


@_register("json_path_equals")
class JsonPathEquals(_Scorer, _PathMixin):
    value: Any
    SUMMARY: ClassVar[str] = "path, value"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        found = self.extract_values(inv)
        return _binary(found == [self.value], f"{self.path}={found!r}")


@_register("json_path_in")
class JsonPathIn(_Scorer, _PathMixin):
    values: list[Any]
    SUMMARY: ClassVar[str] = "path, values"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        found = self.extract_values(inv)
        ok = len(found) == 1 and found[0] in self.values
        return _binary(ok, f"{self.path}={found!r}")


@_register("json_path_subset")
class JsonPathSubset(_Scorer, _PathMixin):
    allowed: list[Any]
    SUMMARY: ClassVar[str] = "path, allowed"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        found = self.extract_values(inv)
        extra = [v for v in found if v not in self.allowed]
        return _binary(not extra, f"unexpected={extra!r}" if extra else "")


# -- continuous ------------------------------------------------------------


@_register("json_path_number")
class JsonPathNumber(_Scorer, _PathMixin):
    KIND: ClassVar[ScorerKind] = "continuous"
    SUMMARY: ClassVar[str] = "path, [min], [max], [aggregate=min], [direction]"

    min: float | None = None
    max: float | None = None
    aggregate: Literal["min", "max", "mean"] = "min"
    direction: Direction = "higher_is_better"

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        numbers = [
            float(v) for v in self.extract_values(inv) if isinstance(v, int | float)
        ]
        if not numbers:
            return (0.0, False, f"{self.path}: no numeric value")
        value = {
            "min": min,
            "max": max,
            "mean": statistics.fmean,
        }[self.aggregate](numbers)
        passed = (self.min is None or value >= self.min) and (
            self.max is None or value <= self.max
        )
        return (float(value), passed, f"{self.path}={value:.4f}")


@_register("latency")
class Latency(_Scorer):
    KIND: ClassVar[ScorerKind] = "continuous"
    DIRECTION: ClassVar[Direction] = "lower_is_better"
    SUMMARY: ClassVar[str] = "[max_s]"

    max_s: float | None = None

    def evaluate(self, inv: Invocation) -> tuple[float, bool, str]:
        passed = self.max_s is None or inv.duration_s <= self.max_s
        return (inv.duration_s, passed, f"{inv.duration_s:.2f}s")

    def reliable_under_concurrency(self) -> bool:
        return False


# -- binding ---------------------------------------------------------------


@dataclass(frozen=True)
class BoundScorer:
    key: str
    type: str
    scorer: _Scorer
    reliable: bool

    def score(self, inv: Invocation) -> ScoreResult:
        value, passed, detail = self.scorer.evaluate(inv)
        direction = getattr(self.scorer, "direction", self.scorer.DIRECTION)
        return ScoreResult(
            key=self.key,
            type=self.type,
            kind=self.scorer.KIND,
            direction=direction,
            value=value,
            passed=passed,
            reliable=self.reliable,
            detail=detail,
        )


def build(spec: ScorerSpec, index: int, *, concurrent: bool = False) -> BoundScorer:
    cls = REGISTRY.get(spec.type)
    if cls is None:
        known = ", ".join(sorted(REGISTRY))
        raise SuiteError(f"unknown scorer {spec.type!r}. known scorers: {known}")
    try:
        scorer = cls(**spec.params())
    except (ValidationError, JsonPathError, re.error) as exc:
        raise SuiteError(f"scorer {spec.type!r}: {exc}") from exc
    reliable = scorer.reliable_under_concurrency() or not concurrent
    return BoundScorer(
        key=f"{index}:{spec.type}", type=spec.type, scorer=scorer, reliable=reliable
    )


def build_all(case: Case, *, concurrent: bool = False) -> list[BoundScorer]:
    return [
        build(spec, i, concurrent=concurrent) for i, spec in enumerate(case.scorers)
    ]


def score_invocation(scorers: list[BoundScorer], inv: Invocation) -> list[ScoreResult]:
    """Errors are a third category — never a silent pass or fail."""
    if not inv.ok:
        return []
    return [s.score(inv) for s in scorers]


def validate_suite(suite: Suite) -> None:
    for case in suite.cases:
        try:
            build_all(case)
        except SuiteError as exc:
            raise SuiteError(f"case {case.id!r}: {exc}") from exc


def describe() -> list[tuple[str, str, str]]:
    return sorted((name, cls.KIND, cls.SUMMARY) for name, cls in REGISTRY.items())
