# noisefloor — a regression harness for non-deterministic systems

**Status:** design approved, awaiting spec review
**Date:** 2026-09-02

## 1. Problem

Most LLM eval tooling answers *"what score does this get?"* The question that
actually blocks a merge is *"did this change make it worse?"* — and on a
non-deterministic system that question is unanswerable without first knowing
how much the system moves on its own.

Run the same prompt twice against an unchanged system and the scores differ.
A harness that reports a delta without reporting the noise around it produces
confident nonsense: it flags sampling wobble as a regression, and a team that
gets burned twice stops reading the output.

`noisefloor` measures the noise before it calls anything a regression.

The through-line with its sibling project: `rag-knowledge-agent` measures the
score floor before gating an answer. This measures the noise floor before
gating a merge.

## 2. Scope

### In scope (v1)

- Suite definition: a target command plus a list of cases with scorers.
- Subprocess adapter: run the target, read JSON from stdout.
- Deterministic scorers only — no model calls, no API key.
- N-repeat execution for both baseline and candidate.
- Per-case, per-scorer noise measurement.
- Baseline save/load, noise-gated diff, non-zero exit on regression.
- Every raw output persisted, so scorers can be added or fixed and old runs
  re-scored offline for free.

### Non-goals (v1) — stated so the plan does not drift into them

- **LLM-as-judge scorers.** They require an API key, which would break the
  rule that the harness's own test suite runs without one. Roadmap item,
  behind an explicit flag.
- **HTTP adapter.** The subprocess adapter covers the first fixture and is
  language-agnostic. Adding a second adapter before the first is proven is
  speculative generality.
- **Multi-turn / stateful targets.** One invocation per case.
- **Cost and token accounting.** The harness cannot see the target's token
  usage through stdout without inventing a protocol.
- **Real statistical tests.** At N=5, a t-test is theatre. See §6.
- **Web UI, dataset generation, prompt optimisation.**

## 3. Architecture

```
suite.yaml ──▶ run (cases × N repeats, subprocess) ──▶ RunRecord
                                                        (raw outputs
                    ┌───────────────────────────────────┐+ scores)
                    │                                   │
              baseline ────────▶ diff (noise-gated) ◀───┘
                                       │
              regressed / broke / improved / fixed / unchanged
              added / removed / redefined
                                       │
                              report + exit code
```

### Modules

Flat and small, matching the sibling repo's shape. Each is independently
testable and holds one responsibility.

| Module | Responsibility | Depends on |
|---|---|---|
| `config.py` | Defaults, paths, env overrides | — |
| `suite.py` | Pydantic models (`Suite`, `Case`, `TargetSpec`, `ScorerSpec`); YAML load, validation, per-case definition hash | `config` |
| `jsonpath.py` | The minimal path subset (§5.2) — its own unit so it can be tested exhaustively | — |
| `target.py` | argv templating, subprocess invocation, timeout, stdout capture and JSON parse → `Invocation` | `config` |
| `scoring.py` | Scorer registry, the nine built-ins, `ScoreResult` | `jsonpath` |
| `run.py` | Orchestration (cases × repeats, optional `--jobs`), `RunRecord` persistence, re-score | `suite`, `target`, `scoring` |
| `stats.py` | Per-case aggregation; the noise band; binary vs continuous split | — (pure functions over numbers) |
| `diff.py` | Baseline vs candidate → `Verdict` | `stats`, `run` |
| `report.py` | Terminal, JSON, and markdown rendering | `diff` |
| `cli.py` | Argparse subcommands, exit codes, strict stdout/stderr separation | all |

## 4. Suite format

YAML, not TOML. Case inputs are prompts — often multi-line — and YAML block
scalars (`|`) read far better than TOML's escaping for that. The cost is one
pinned dependency (`pyyaml`); the benefit is a suite file a human will
actually edit.

```yaml
name: rag-knowledge-agent
target:
  # argv list. Never a shell string — see §5.1.
  command: ["rag", "ask", "--json", "{{input}}"]
  cwd: ../rag-knowledge-agent      # resolved relative to the suite file
  timeout_s: 60
defaults:
  repeats: 5

cases:
  - id: offline-behaviour
    input: |
      A Meridian-3 has stopped reporting. How long can it keep working
      offline, and how would I detect that from the Fleet Control API?
    scorers:
      - json_valid
      - {type: json_path_in, path: confidence, values: [high, medium]}
      - {type: json_path_subset, path: "citations[].source",
         allowed: [product-specs.md, api-reference.md, troubleshooting.md]}
      - {type: contains, needle: "offline"}

  - id: refuses-off-topic
    input: What is Acme Robotics' parental leave policy?
    scorers:
      - {type: contains, needle: "I don't know"}
      - {type: json_path_equals, path: confidence, value: low}
```

`defaults.repeats` may be overridden per case and on the command line.

## 5. Target adapter

### 5.1 argv templating — no shell, ever

`command` is a list. `{{input}}` (and `{{case_id}}`) are substituted into
*individual argv elements*, and the process is spawned without a shell.

This is not a stylistic preference. A shell string would make a case input of
`"; rm -rf ~"` a command-injection vector — in a harness whose first fixture
is a project that advertises prompt-injection defense. A test asserts that an
input containing shell metacharacters arrives at the target as one argv
element, unmodified.

### 5.2 Reading the result

stdout is parsed as JSON. The `jsonpath` module implements a deliberately
tiny subset, enough for the scorers and no more:

- `confidence` — top-level key
- `citations[0].source` — indexed element
- `citations[].source` — every element, yielding a list

Anything else is a suite validation error at load time, not a runtime
surprise.

### 5.3 Invocation outcomes

Every repeat records `stdout`, `stderr`, `exit_code`, `duration_s`, and one
outcome:

| Outcome | Meaning | Scored? |
|---|---|---|
| `ok` | exit 0, stdout parsed | yes |
| `error:exit` | non-zero exit | no |
| `error:timeout` | exceeded `timeout_s` | no |
| `error:parse` | stdout is not JSON | no |

**Errors are a third category, never a silent pass or fail.** A case with at
least one `ok` repeat and at least one error is `degraded`; a case with zero
`ok` repeats is `error`.

## 6. Scoring and the significance rule

This section is the product. Everything else is plumbing.

### 6.1 Scorers

Every scorer emits `{value: float, passed: bool, kind: binary|continuous}`.

**Binary** (value 0.0 or 1.0): `contains`, `not_contains`, `regex`,
`json_valid`, `json_path_equals`, `json_path_in`, `json_path_subset`.

**Continuous** (value is the measurement; `passed` from optional bounds, plus
a required `direction` of `higher_is_better` or `lower_is_better`):
`json_path_number` (bounds `min`/`max`), `latency` (bound `max_s`).

### 6.2 Repeats on both sides

Both the baseline and the candidate run N repeats, default 5. Comparing a
single candidate draw against a baseline distribution reintroduces exactly
the false positives that measuring noise was meant to remove. `--repeats`
sets both; a mismatch between a stored baseline's N and the candidate's N is
allowed but printed in the report, because it widens the uncertainty.

### 6.3 The rule, split by scorer kind

A single "delta exceeds the observed range" rule breaks on binary scorers,
where the range degenerates: a unanimous 5/5 baseline has range 0 (so *any*
failure looks significant), while a 3/5 baseline has range 1 (so a candidate
collapse to 0/5 reads as noise). Two rules, then.

**Binary — compare pass rates out of N.** Regression when either holds:

1. The baseline passed unanimously and the candidate did not. A clean
   baseline observed no variance, so any failure is new behaviour. This is
   deliberately strict and will occasionally fire on a rare flake the
   baseline did not sample — the report always shows `5/5 → 4/5` so a human
   can overrule it.
2. `baseline_rate − candidate_rate > min_rate_drop` (default `0.2`).

At N=5 this means: from a unanimous baseline, one failure is a regression;
from a flaky baseline, one further failure is not, but two are.

**Continuous — range plus effect size.** Regression when *both* hold:

1. The candidate mean falls outside the baseline's observed range, on the bad
   side of it (per `direction`).
2. `|baseline_mean − candidate_mean| > min_effect` (per-scorer, default 0).

With the default `min_effect` of 0 only the range clause binds, which is the
right starting point: set an effect size when you know what size of change you
care about, not before.

A continuous scorer with bounds also produces a pass rate, and the binary
rule applies to that as well. Either firing is a regression.

### 6.4 Verdict categories

Defined once, since the rest of the document uses them:

| Verdict | Condition |
|---|---|
| `regressed` | §6.3 fired against the baseline |
| `improved` | §6.3 fired with the sign reversed |
| `unchanged` | neither |
| `broke` | baseline had ≥1 `ok` repeat, candidate has none |
| `fixed` | candidate has ≥1 `ok` repeat, baseline had none |
| `added` / `removed` | case id present on only one side |
| `redefined` | case definition hash differs — excluded from the verdict |

Only `regressed` and `broke` affect the exit code. `improved` is reported
under the same rule as `regressed` so that a suspiciously large gain gets the
same scrutiny as a loss.

### 6.5 Honesty about the statistics

The observed range over 5 samples **is not a confidence interval**, and this
is stated in the README, not buried. Dressing it up as a hypothesis test
would be the exact false precision this project exists to avoid. The rule is
a heuristic tuned to keep CI false-positive rates low, and every report
prints the underlying numbers so the human can disagree with it.

When N=1, the report says `noise unmeasured` and the harness **refuses to
call anything significant** — it shows deltas and exits 0.

## 7. Comparison and identity

- Cases are matched **by id**.
- Ids present in only one run are reported as `added` / `removed` and never
  counted as regressions.
- Each case carries a **definition hash** over its input and scorers. If it
  changed, the case is `redefined`: reported, excluded from the verdict.
  This is what the stored suite hash gates — a suite may evolve without
  invalidating the whole baseline, but a case may not silently change
  meaning underneath its own id.
- If the **target command differs** between baseline and candidate, `diff`
  refuses by default. `--allow-target-change` proceeds with a loud warning,
  because comparing two different programs is occasionally what you want
  (a model swap) and usually a mistake (a stale baseline).

## 8. Storage

```
.noisefloor/
  runs/<run-id>/
    run.json                      metadata: suite hash, per-case definition
                                  hashes, target argv, cwd, git SHA, repeats,
                                  started/finished, harness version
    cases/<case-id>/<n>.json      stdout, stderr, exit_code, duration_s, outcome
    scores.json                   per case, per repeat, per scorer
  baselines/<suite-name>.json     {run_id, set_at}
```

`run-id` is `<utc-timestamp>-<suite-name>-<suite-hash8>` — sortable and
self-describing. `--out` overrides the root.

Persisting raw stdout pays twice: a new or corrected scorer re-scores every
historical run offline (`noisefloor rescore`), and committed `RunRecord`
fixtures make the harness's own diff and report tests key-free.

## 9. CLI

| Command | Purpose |
|---|---|
| `noisefloor run <suite> [--repeats N] [--jobs N] [--out DIR]` | Execute, persist, print a summary |
| `noisefloor check <suite>` | Run, then diff against the current baseline — the CI one-liner |
| `noisefloor diff [<run-id>] [--baseline <run-id>]` | Compare and set the exit code |
| `noisefloor baseline set <run-id>` / `baseline show` | Manage the comparison point |
| `noisefloor rescore <run-id> [--suite S]` | Re-apply scorers to stored outputs |
| `noisefloor show <run-id> [--case ID]` | Inspect raw outputs |
| `noisefloor scorers` | List built-ins and their parameters |

`--format terminal|json|markdown` on the reporting commands. Reports go to
stdout, diagnostics to stderr, so `noisefloor diff --format json | jq` works.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No regression |
| 1 | At least one case regressed |
| 2 | At least one case broke (target failed where the baseline succeeded) |
| 3 | Harness or configuration error (invalid suite, missing baseline) |

`broke` outranks `regressed` when both occur — a crashing target is the more
urgent finding.

### Concurrency and latency

Serial by default. `--jobs N` parallelises case execution, which is the
difference between a 4-minute and a 40-second baseline at 20 cases × 5
repeats — but it invalidates the `latency` scorer. Under `--jobs > 1`,
latency results are marked `unreliable`, excluded from the verdict, and the
report says so. Silently reporting contended timings as measurements would
be the same sin the project is built to avoid.

## 10. Testing — no API key, ever

`tests/fake_target.py` is a deterministic CLI that can be told to be flaky
(`--flake-seed`, varying by repeat index), slow (`--delay`), broken
(`--exit-code`), or to emit garbage (`--garbage`). It makes the whole matrix
of behaviours reachable without a model.

Tests that must exist:

- Identical runs → zero regressions.
- **Wobble inside the baseline's observed band → not flagged.** This is the
  central claim; if it fails, the project has no reason to exist.
- Injected degradation → flagged.
- Baseline 5/5, candidate 4/5 → flagged (unanimous-baseline clause).
- Baseline 3/5, candidate 3/5 → not flagged; baseline 3/5, candidate 0/5 →
  flagged (the degenerate-range guard from §6.3).
- Candidate exits non-zero where the baseline succeeded → `broke`, distinct
  from `regressed`, exit code 2.
- Timeout and non-JSON stdout → recorded as errors, not scored.
- Added / removed / redefined cases never counted as regressions.
- `rescore` reproduces byte-identical scores from stored outputs.
- Shell metacharacters in a case input reach the target as one argv element.
- Every exit code is asserted.

Diff and report tests run against committed `RunRecord` fixtures — no
subprocess, no clock, no network.

## 11. First fixture: rag-knowledge-agent

`examples/rag-knowledge-agent/suite.yaml` targets `rag ask --json` unchanged,
with on-topic cases (expect `high`/`medium` confidence, citations drawn from
the known source set) and off-topic cases (expect the refusal sentence and
`low` confidence). A baseline `RunRecord` is committed so `noisefloor diff`
demonstrations work offline; regenerating it costs one API key and roughly
100 calls.

### The measurement this project owes its README

**What is the actual run-to-run variance of `rag ask --json` on the sample
corpus?** Nobody has measured it. It is published in the README whatever it
says, the way the retrieval calibration numbers were.

A prediction, recorded now so it can be wrong in public: the RAG agent sets
no `temperature`, so it samples at the API default — meaning **answer text
should vary while structure does not**. Retrieval is deterministic, and
confidence is derived from retrieval scores, so `confidence` and
`citations[].source` should be stable at 5/5 while a `contains` scorer over
prose wobbles. If that holds, it is the best possible argument for
per-scorer noise measurement rather than one global "is it flaky" number.

**If measured variance turns out to be near zero across every scorer**, the
noise-floor premise softens and the README says so plainly. The harness would
still earn its place on three grounds — regression detection across model
swaps, prompt edits, and retrieval-mode changes; the raw-output cache that
makes scorer development free; and the fact that a measured zero is itself
the result a reader wants. Publishing that negative would follow the same
pattern as the hybrid-search premise that turned out to be wrong in the
sibling project.

## 12. Dependencies and conventions

- Python ≥ 3.11. Runtime deps: `pydantic`, `pyyaml` — pinned. Dev: `pytest`,
  `ruff`.
- Type hints throughout; ruff-clean; entry point `noisefloor`.
- `.gitignore`: `.noisefloor/`, `__pycache__/`, `*.egg-info`.
- Repo-relative paths only in anything committed.

## 13. Roadmap (explicitly not v1)

HTTP adapter · LLM-as-judge scorer behind a flag · bootstrap confidence
intervals once N can be large · GitHub Action wrapper · cost accounting if a
target protocol for reporting usage emerges.

## 14. Open items

- The name is provisional. Renaming is cheap before the first push and
  expensive after; if `noisefloor` is not the final answer, say so before
  implementation starts.
- Nothing is published anywhere until that is a separate, explicit decision.
