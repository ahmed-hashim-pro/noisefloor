# Example: rag-knowledge-agent

`suite.yaml` targets
[rag-knowledge-agent](https://github.com/ahmed-hashim-pro/rag-knowledge-agent)
unmodified, through its `rag ask --json` command. Five cases, each checking
a different combination of confidence, citation provenance, and answer
content — no single description covers all five, so here is what each one
actually asserts:

| Case | Checks |
| --- | --- |
| `offline-behaviour` | on-topic; confidence `high`/`medium`, citation sources against the measured `allowed` list, answer mentions "offline" |
| `charging-bays` | on-topic; confidence `high`/`medium`, lowest citation score >= 0.35 (no `aggregate` set, so it defaults to `min` — every citation must clear the bar, not just the best one) |
| `error-code-409` | on-topic; answer mentions "409", citation sources against the measured `allowed` list — no confidence assertion |
| `refuses-parental-leave` | off-domain but near the confidence floor; the refusal sentence and confidence `low` — retrieves above the floor (0.436 vs. a 0.35 confidence floor), so whether it would gate to the canned refusal was a genuine unknown until measured; see the comment in `suite.yaml` |
| `refuses-off-domain` | off-domain, retrieves nothing; confidence `low`, answer does not mention "flour" — no refusal-sentence assertion |

## Running it

This suite does not run standalone — it needs a sibling checkout of
`rag-knowledge-agent`, ingested once:

```bash
cd ../rag-knowledge-agent
pip install -e .
rag ingest sample_corpus
cd ../noisefloor
noisefloor check examples/rag-knowledge-agent/suite.yaml
```

`target.cwd` in `suite.yaml` is `../../../rag-knowledge-agent`, resolved
relative to the suite file itself, so this only works if `rag-knowledge-agent`
lives as a sibling directory to this `noisefloor` checkout.

`rag` also has to be on the **PATH of the shell that runs `noisefloor`** —
`target.command` invokes it by bare name (`rag ask --json ...`), not by
path, so `pip install -e .` has to have put it somewhere that shell's PATH
resolves. Get this wrong and the harness does the right thing: the actual
measurement's first capture attempt failed for exactly this reason, and
`noisefloor` recorded it correctly as an `error:exit` outcome with the cause
in stderr, scored nothing, and cost no API calls. That's the error handling
working as intended — but you'll hit the same wall if `rag` isn't resolvable
from wherever you invoke `noisefloor` from.

## The committed baseline

A baseline `RunRecord` is committed under `baseline/` — captured against
`claude-sonnet-4-6` (no `temperature` set, so it samples at the API
default), at 3 repeats per case rather than this suite's default of 5, to
limit API spend: 15 model calls, 3 minutes 9 seconds wall clock, all 5 cases
`ok`. See the main [README.md](../../README.md#measured-how-noisy-is-a-real-rag-agent)
and [`docs/design-notes.md` §8](../../docs/design-notes.md#8-measured-run-to-run-variance-of-a-real-rag-agent)
for the full write-up, including a false positive the capture found in
`noisefloor` itself.

Because the baseline is committed, comparing against it —
`noisefloor --root examples/rag-knowledge-agent/baseline diff` — needs
nothing: no key, no network, and (with no `run_id` given) it diffs the latest
stored run against the baseline. Only *regenerating* the baseline, or running
a fresh `check` to diff against it, needs `ANTHROPIC_API_KEY` and a live
`rag-knowledge-agent`.

## The `allowed` lists

The `json_path_subset` scorers' `allowed` lists were set from a real,
offline retrieval run against the sample corpus — see the comment at the top
of `suite.yaml`. They are deliberately not widened past what was measured.
