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
| `refuses-parental-leave` | off-domain but near the confidence floor; the refusal sentence and confidence `low` — see the comment in `suite.yaml`: this one retrieves above the floor, so the verdict is a genuine unknown until measured, not a safe bet |
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

## API key

Reading the committed baseline needs nothing — no key, no network. Only
*regenerating* it does: each `noisefloor check` or `noisefloor run` invokes
the target once per case per repeat, which at this suite's defaults (5 cases
x 5 repeats) is roughly 25 calls to whatever model `rag-knowledge-agent` is
configured to use, and needs `ANTHROPIC_API_KEY` set in the environment
`rag ask` runs in.

## The `allowed` lists

The `json_path_subset` scorers' `allowed` lists were set from a real,
offline retrieval run against the sample corpus — see the comment at the top
of `suite.yaml`. They are deliberately not widened past what was measured.
