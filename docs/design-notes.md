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

**Naming collision, not a design decision:** `CaseRun.outcome` above uses
`degraded` for a per-run fact (some but not all repeats errored). `diff.py`
separately warns `case 'x' degraded: ...` when a candidate's ok *rate* drops
below its baseline's — a comparison across two runs, not a property of one.
They're unrelated conditions that happen to share a word; a case can be
`degraded` in the first sense on both sides of a diff and never trigger the
second, or vice versa. Predates this note; recorded here rather than fixed
by renaming either one.

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

Captured against the fixture in `examples/rag-knowledge-agent/`:
[rag-knowledge-agent](https://github.com/ahmed-hashim-pro/rag-knowledge-agent),
model `claude-sonnet-4-6`, no `temperature` set, over a corpus of 44 chunks
from 5 files. Two captures — a baseline, then the identical suite against a
target that had not changed at all — each at 3 repeats per case rather than
the suite's default of 5, to limit API spend: 5 cases x 3 repeats x 2
captures = 30 model calls. That thinner N buys a coarser noise estimate than
the suite's own default would; said plainly rather than left unremarked. The
baseline capture ran 15 calls in 3 minutes 9 seconds wall clock, all 5 cases
`ok`.

The result: `5 unchanged`, exit `0`, no warnings — 15 binary scorer
instances all stable at 3/3 -> 3/3, and one continuous scorer
(`json_path_number` on `charging-bays`) the only thing that moved, from
`0.543 [0.437–0.597] n=3` to `0.597 [0.597–0.597] n=3`. The raw stored output
shows why: retrieval is exactly deterministic — `product-specs.md` scores
0.5965 on every repeat, `faq.md` 0.4374 on every repeat it appears — and what
moved was how many citations the model chose to emit, not what retrieval
returned. The scorer aggregates with `min`, so the one extra citation pulled
the observed band down.

The spec's own falsifiable prediction — prose varies, structure (`confidence`,
`citations[].source`) does not, because sampling happens at the API default
while retrieval is deterministic — held on the structural half and was never
actually exercised on the prose half: no scorer here can distinguish a stable
answer from a reworded one, since the assertions (`contains "409"`, and
similar) are substring checks robust to rewording by construction. That is a
gap in the suite's coverage, not evidence that the prose was stable. The
variance that did show up came from neither predicted axis — it came from
citation count, a third source the prediction did not name.

### What the measurement found in the tool

This is the more important half of this section, because it is a case of the
theory in §2 and §3 meeting a real system and a real bug surviving past both.

The first run of the second capture did not report 5 unchanged. It reported
`1 improved, 4 unchanged` on `charging-bays` — `noisefloor` claiming a change
on a system that had not changed, in the *improvement* direction. A false
positive is a false positive regardless of which direction it points, and
this is the exact failure mode the whole significance-rule design exists to
prevent.

The cause was the continuous range clause (§2, spec 6.3) sharing plumbing
with the two binary clauses. At the time, both binary clauses were
implemented behind a single mirrored call, `_worse(before, after)`, called
once each way to get the regressed and improved verdicts. That mirroring is
correct for the rate-drop clause, because a pass-rate delta past a threshold
reads the same from either side, with no asymmetric "reference" role for
either aggregate — but it was not correct for the unanimous-baseline clause,
which turned out to share the same baseline-as-reference asymmetry as the
continuous clause below; see the addendum at the end of this section for
that fix, discovered later. The continuous clause was implemented the same
mirrored way here, and that is where it broke first: on this capture the candidate's
three citation-score values were all identical (`0.5965` x 3), so the
candidate's own observed band had zero width. The mirrored call put that
zero-width *candidate* band in the reference role for the improvement
direction, and against a single-point band, any baseline value at all reads
as "outside" it. This is the identical degenerate-range failure that
motivated splitting the binary rule in the first place — a rule built on
"outside the observed band" breaks whenever the wrong side's band happens to
collapse to a point. The continuous rule had the same hole from the start; it
just took a sample landing with zero spread to surface it, which is exactly
what happened on `charging-bays`'s candidate side.

Fixed in `ade90b8` by pulling the continuous clause out of `_worse` into its
own function, `_continuous_move`, which always tests the candidate's mean
against the **baseline's** observed band, in both directions, and never
substitutes the candidate's own spread for it — because the baseline is the
side deliberately measured as the noise reference; only its band means
anything as a yardstick. A first attempt at this fix went further and
refused to call anything significant off *any* zero-width band, on either
side. That overcorrected: it made a deterministic `0.9 -> 0.1` collapse read
as `unchanged`, which is worse than the bug it fixed, because a zero-width
*baseline* band is not an absence of information — it is the strongest
evidence available. If the baseline never moved across its repeats, a
candidate difference cannot be attributed to sampling noise, since there was
no sampling noise observed to attribute it to. The final version keeps that
distinction: a degenerate baseline band is decisive; a degenerate candidate
band must never stand in for it.

Two things are worth drawing out beyond the fix itself. First, ten review
passes and 182 tests did not surface this — one 15-call capture against a
real system did. Coverage built entirely from hand-constructed fixtures
shares whatever blind spot the person constructing them has; nobody wrote a
test where the candidate happened to be a repeated point, because nobody
was looking for that shape until a real target produced it. Second,
validating the fix cost zero additional API calls: every raw output from
both captures was already stored under `.noisefloor/runs/` (§5) before the
bug was found, so confirming the fix was `noisefloor diff` re-running the
corrected rule against runs that had already been paid for. The cache
described in §5 as saving money on scorer iteration turned out to save it
identically on statistics-engine iteration — it paid for itself the first
time this project needed to debug itself against real data.

Also settled by this capture: `refuses-parental-leave` retrieves at a top
score of 0.436, above the agent's 0.35 confidence floor, so whether it would
gate to the canned refusal was a genuine open question, not a safe bet (see
the comment that carried this in `examples/rag-knowledge-agent/suite.yaml`
until this measurement). Measured: it refuses cleanly — `low` confidence,
zero citations, 3/3 on every scorer, every repeat.

**Addendum: the unanimous-baseline clause had the same defect (issue #7).**
The mirroring flagged above as "correct there" for the binary clauses was
only correct for the rate-drop half. The unanimous-baseline half was still
being evaluated by a swapped `_worse` call, which meant a candidate reaching
5/5 from a baseline that had already shown variance (4/5) was reported as
`improved` — the identical shape as the continuous-clause bug in this
section, just on the binary side and without a real-target capture to
surface it. A 4/5 → 5/5 move and a 3/5 → 2/5 move carry the same 0.200 rate
delta; only the first was flagged, decided solely by which side happened to
land on unanimity. Fixed by pulling the clause out of `_worse` into its own
function, `_unanimous_break`, which — like `_continuous_move` — always tests
the baseline's own pass rate and is never called with arguments swapped. The
rate-drop clause alone now carries the symmetric improvement direction for
binary scorers.

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
