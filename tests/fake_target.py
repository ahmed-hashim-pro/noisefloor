#!/usr/bin/env python3
"""A deterministic stand-in for a real target.

Every failure mode the harness must handle is reachable from flags, so no test
needs a model, a network, or a random seed. Flakiness is a function of the
repeat index, which makes a "3 out of 5 pass" baseline exactly reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    # Env fallbacks (not just flags) let a suite vary the target's behavior
    # while its command argv — and therefore RunRecord.target_command — stays
    # byte-identical, for tests that must not trip diff.py's TargetChanged.
    parser.add_argument(
        "--answer",
        default=os.environ.get(
            "NOISEFLOOR_FAKE_ANSWER", "the robot parks after twelve minutes"
        ),
    )
    parser.add_argument("--confidence", default="high")
    parser.add_argument("--source", default="a.md")
    parser.add_argument("--score", type=float, default=0.7)
    parser.add_argument(
        "--exit-code",
        type=int,
        default=int(os.environ.get("NOISEFLOOR_FAKE_EXIT_CODE", "0")),
    )
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--garbage", action="store_true")
    parser.add_argument("--stderr", default="")
    parser.add_argument("--flake-every", type=int, default=0)
    parser.add_argument("--fail-every", type=int, default=0)
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--echo-argv", action="store_true")
    parser.add_argument("question", nargs="?", default="")
    args = parser.parse_args()

    if args.delay:
        time.sleep(args.delay)
    if args.stderr:
        print(args.stderr, file=sys.stderr)

    if args.echo_argv:
        print(json.dumps({"argv": sys.argv[1:]}))
        return 0
    if args.garbage:
        print("not json at all <html>")
        return args.exit_code

    if args.fail_every > 0 and args.repeat % args.fail_every == 0:
        print("simulated per-repeat failure", file=sys.stderr)
        return 1

    flaking = args.flake_every > 0 and args.repeat % args.flake_every == 0
    payload = {
        "answer": "unrelated filler" if flaking else args.answer,
        "confidence": "low" if flaking else args.confidence,
        "citations": [{"source": args.source, "score": args.score}],
        "question": args.question,
    }
    print(json.dumps(payload))
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
