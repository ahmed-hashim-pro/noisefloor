"""A deliberately tiny path subset: dotted keys, `[i]`, and `[]`.

Anything richer would be a second query language to learn, test, and document.
Paths are parsed when a suite loads, so a typo is a validation error rather than
a silent empty result halfway through a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)((?:\[\d*\])*)$")
_SUBSCRIPT = re.compile(r"\[(\d*)\]")


class JsonPathError(ValueError):
    """A path that cannot be parsed."""


@dataclass(frozen=True)
class _Key:
    name: str


@dataclass(frozen=True)
class _Index:
    position: int


@dataclass(frozen=True)
class _Wildcard:
    pass


_Segment = _Key | _Index | _Wildcard


@dataclass(frozen=True)
class JsonPath:
    raw: str
    segments: tuple[_Segment, ...]

    @classmethod
    def parse(cls, raw: str) -> JsonPath:
        if not raw or not raw.strip():
            raise JsonPathError("path is empty")
        segments: list[_Segment] = []
        for part in raw.split("."):
            match = _SEGMENT.match(part)
            if match is None:
                raise JsonPathError(f"{raw!r}: cannot parse segment {part!r}")
            segments.append(_Key(match.group(1)))
            for subscript in _SUBSCRIPT.findall(match.group(2)):
                segments.append(
                    _Wildcard() if subscript == "" else _Index(int(subscript))
                )
        return cls(raw=raw, segments=tuple(segments))

    @property
    def is_multi(self) -> bool:
        return any(isinstance(seg, _Wildcard) for seg in self.segments)

    def extract(self, data: Any) -> list[Any]:
        current: list[Any] = [data]
        for segment in self.segments:
            current = list(_step(current, segment))
        return current


def _step(values: list[Any], segment: _Segment):
    for value in values:
        match segment:
            case _Key(name):
                if isinstance(value, dict) and name in value:
                    yield value[name]
            case _Index(position):
                if isinstance(value, list) and position < len(value):
                    yield value[position]
            case _Wildcard():
                if isinstance(value, list):
                    yield from value
