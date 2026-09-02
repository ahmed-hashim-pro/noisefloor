# Design notes

Why `noisefloor` is shaped the way it is. The code is the source of truth for
*what* it does; this document is for *why*, where the why is not obvious from
reading `noisefloor/`.

## 1. Why regression-first, not scoring

Most eval tooling answers "what score does this get?" — a single run, a single
number, compared against a threshold or a vibe. That question is well-posed for
a deterministic function. It is not well-posed for a system that samples: run
the same prompt against an unchanged target twice and the score moves, so a
single before/after comparison cannot distinguish "this got worse" from "this
drew a different sample." A CI gate built on that comparison either never fires
(thresholds set loose enough to absorb the wobble) or fires on every run
(thresholds set tight enough to mean something), and a team that gets burned by
the second case stops trusting the gate.

The question that actually blocks a merge is "did this change make it worse?" —
which is a claim about two distributions, not two numbers. `noisefloor` treats
scoring as an input to that comparison, not the product. It runs N repeats on
both sides of a change, so the noise the system produces on its own is measured
before anything is called a regression against it.

## 2. Why the range rule needed splitting

The first instinct is one rule: flag a regression when the candidate falls
outside the range the baseline was observed to occupy. That rule breaks on
binary scorers because pass/fail counts over N repeats produce a range that
degenerates at both ends of the scale.

Work the two cases through at N=5, where each repeat scores 0.0 or 1.0 and
"the observed range" means the spread between the smallest and largest
per-repeat value. A baseline that passes unanimously (5/5) has every repeat at
1.0, so its range is exactly 0. Under a naive "candidate outside the baseline
range" rule that is the correct, strict outcome: a single candidate failure —
even one flake the baseline never happened to sample — falls outside a
zero-width range and is rightly new information, because a clean baseline
demonstrated no variance to absorb it. But a baseline that already flakes
(3/5) has both a 1.0 and a 0.0 among its five repeats, so its range spans the
entire scale, 0 to 1, width 1. Every possible candidate value already sits
inside that range — so the same naive rule would wave through a total
collapse to 0/5 as "inside the observed range," which is exactly backwards:
the whole point of measuring five repeats was to catch that.

So range alone cannot be the rule for binary scorers: it is too strict for a
clean baseline (correctly) and too permissive for a flaky one (wrongly), and
no single width threshold fixes both at once. Binary scorers are compared on
pass rate instead — a drop of more than `--min-rate-drop` (default `0.2`), so
at N=5 one extra failure from a flaky baseline is absorbed but two are not —
plus the unanimous-baseline clause carried over from the range intuition
where it was actually correct. Continuous scorers keep the range idea, because
for a real-valued measurement "the candidate mean landed outside where the
baseline was ever observed" is meaningful on its own; it is paired with a
configurable minimum effect size for when width alone would be too eager.
The default effect size is `0.0`, so out of the box only the range clause
binds and any amount outside the observed range fires — the right starting
point, since a size worth ignoring is something you set once you know what
scale of change you actually care about, not something the harness should
guess at. Splitting the rule by scorer kind is what lets both the
unanimous-baseline case and the already-flaky case land on the correct
verdict without a hand-tuned exception for either.

## 3. Why not a t-test

A t-test (or any parametric significance test) asks for more than five samples
can honestly give it: a distributional assumption, an estimate of variance
that itself has almost no degrees of freedom at N=5, and a p-value whose
precision implies a confidence the sample size cannot support. Running one
anyway would produce a number that looks rigorous — "p < 0.05" reads as
authoritative — while resting on an estimate built from four or five draws.
That is false precision, and false precision is the exact failure mode this
project exists to avoid: a confident-looking wrong answer is worse than a
visibly rough one, because it gets trusted without being checked.

The chosen alternative is a heuristic — an observed range plus a minimum
effect size — that makes no claim to statistical rigor and says so directly in
the README and in every report line. It is tuned by hand to keep CI false
positives low at the sample size this harness actually runs by default
(N=5) — an order where a parametric test has no leg to stand on — and it
prints the raw counts behind every verdict so a human can overrule it. When N
drops to 1, the harness does not even attempt the heuristic — it reports
`noise unmeasured` and refuses to call anything
significant, which is the honest answer when there is no basis for any other
one. Bootstrap confidence intervals are on the roadmap for exactly the
scenario where they'd stop being theatre: once a suite can afford enough
repeats that resampling has something to resample.

## 4. Why argv, never a shell

`target.command` is a list of argv elements, and the target is spawned with
`shell=False`. This is a security property, not a style preference. Case
inputs are arbitrary text supplied by whoever writes the suite — often
copy-pasted from a bug report or a support ticket — and if that text were
interpolated into a shell string, a case input like `"; rm -rf ~"` would not
be data, it would be a command. Building the harness on a shell string would
make every suite a potential injection vector, and the irony would be
particular here: this project's first fixture is `rag-knowledge-agent`, which
advertises defending against prompt injection. A harness for that project that
was itself injectable through its own case inputs would undercut the thing it
is testing.

Substitution happens per argv element (`{{input}}`, `{{case_id}}`,
`{{repeat}}`), and the replacement is a single regex pass rather than chained
string replacement — chained replacement would re-scan already-substituted
text, so a case input that happened to contain the literal string `{{repeat}}`
would get replaced a second time instead of passing through as inert data.
The cost of this design is that a target expecting shell features — pipes,
globbing, environment expansion in the command line itself — cannot get them.
That cost is intentional: those features are exactly the surface that makes
shell injection possible.

## 5. Why raw outputs are cached

Every stdout, stderr, exit code, and duration from every repeat is persisted
under `.noisefloor/runs/` at run time, before any scorer touches it. This pays
for itself twice. First, scorer development becomes free: adding a scorer, or
fixing a bug in an existing one, does not require re-running the target —
`noisefloor rescore` re-applies the current scorer set to output that was
already paid for, whether that payment was API dollars or just wall-clock
time. Iterating on what "passing" means for a case should not cost another N
model calls every time a path expression gets corrected.

Second, and just as load-bearing: the same `RunRecord` shape that gets
persisted to disk is what the tests build directly in Python instead of
capturing from a real target. `tests/test_diff.py` constructs
`RunRecord`/`CaseRun`/`Invocation` objects by hand with small helper functions,
and `tests/test_report.py` reuses those same helpers, feeding their output
through `diff_runs` before rendering — so the whole comparison and verdict
pipeline, plus every report format, is exercised deterministically with no
subprocess, no network, no clock, and no model call. Because it is the same
`RunRecord` shape that `run.py` writes under `.noisefloor/runs/` for a real
invocation, a hand-built test fixture and a run captured from a real target are
interchangeable inputs to `diff` and `report` — which is also what makes
`rescore` possible: it replays the identical shape through a different (or
corrected) scorer set rather than through the comparison logic.

## 6. Why errors are a third category

A target that exits non-zero, times out, or prints something that fails to
parse as JSON is recorded as an `error` outcome, distinct from both a pass and
a fail. It is deliberately never scored as a 0 — a crash is not a low score,
it is the absence of an answer to score, and folding it into the same scale as
"scored zero" would let a broken target hide inside ordinary pass-rate noise
until the pass rate happened to cross a threshold.

The distinction carries through to the verdict layer: a case that had at least
one `ok` repeat in the baseline but zero in the candidate is reported as
**broke**, not **regressed**, and `broke` gets its own exit code (2, ranked
above `regressed`'s 1) because "the target crashes now" is a more urgent
finding than "the target got somewhat worse" and a CI consumer should be able
to tell the two apart without reading the report body. Symmetrically, a case
recovering from zero `ok` repeats to at least one is `fixed`, not merely
`improved`.

## 7. Why YAML over TOML

Suite files are prompts plus assertions, and prompts are usually multi-line
prose — the kind of text a person actually wants to read and edit in place,
not escape. YAML's block scalar (`|`) preserves a multi-line string with its
line breaks intact and no escaping, which is exactly the shape of `input:` in
every case in this project's own example suite. TOML's answer to the same
problem is a quoted string with explicit `\n` escapes or a basic multi-line
string with its own quoting rules — technically capable, but nobody wants to
hand-edit a three-sentence customer question through either of those forms.

The cost of choosing YAML is one more pinned runtime dependency (`pyyaml`,
alongside `pydantic` for validation) and YAML's well-known sharp edges
(implicit typing surprises like the Norway-Boolean problem, whitespace
sensitivity). Those costs were judged smaller than the alternative: a suite
format that punishes the exact content — long, human-authored prompts — this
tool exists to run.

## 8. Measured: run-to-run variance of a real RAG agent

<!-- MEASURED: filled in by Task 12 -->

## 9. Deliberately missing

Every non-goal below was excluded on purpose, not by oversight, and each has a
reason tied to what this project is trying to prove first.

- **LLM-as-judge scorers.** A judge call needs an API key, and this harness's
  own test suite is a load-bearing proof that noise measurement requires
  neither a key nor the network. Adding a judge scorer as a first-class
  built-in would compromise that property for every user, not just the ones
  who want it — so it stays a roadmap item, gated behind an explicit flag,
  rather than something the harness defaults into.
- **An HTTP adapter.** The subprocess adapter is language-agnostic and already
  covers the first real fixture end to end. Building a second transport before
  the first one has been exercised against a real target is speculative
  generality — effort spent on a need that has not yet been demonstrated,
  instead of on the significance rule, which is the part of the project that
  actually needed to be right.
- **Multi-turn or stateful targets.** One invocation per case keeps the noise
  measurement well-defined: N independent repeats of the same input. A
  stateful, multi-turn target introduces a second axis of variation
  (conversation history) that the current statistics do not model, and mixing
  the two would make a regression report ambiguous about which axis moved.
- **Cost and token accounting.** The harness only sees a target's stdout; it
  has no visibility into token usage without the target adopting a reporting
  protocol that does not exist yet. Inventing one to serve this feature would
  be designing a second product inside the first.
- **A real statistical test (t-test, confidence intervals).** Covered in
  detail in §3 — at the sample sizes this harness runs by default, a
  parametric test manufactures precision the data does not support.
- **A web UI, dataset generation, or prompt optimisation.** Each is a
  separate, sizeable product with its own scope. `noisefloor` is a
  regression gate, not an authoring environment or an optimizer; scope creep
  into any of these would dilute the one question the tool is built to
  answer.
