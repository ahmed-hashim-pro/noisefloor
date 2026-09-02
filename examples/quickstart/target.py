#!/usr/bin/env python3
"""A tiny deterministic stand-in for a real agent.

Used by the suite in this directory and by the README's opening example: no
model, no network, no sibling checkout, so `noisefloor check` runs on it from
a fresh clone.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else ""
    if "capital" in question.lower():
        answer = "Paris is the capital of France."
        confidence = "high"
    else:
        answer = "I don't know."
        confidence = "low"
    print(json.dumps({"answer": answer, "confidence": confidence}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
